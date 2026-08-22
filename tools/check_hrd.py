#!/usr/bin/env python3
"""Independent Annex C CPB verifier for H.264/AVC streams.

Parses SPS VUI HRD parameters, buffering_period / pic_timing SEI messages and
slice headers of an Annex B stream, then simulates the coded picture buffer at
access unit granularity per C.1.1/C.1.2 (CBR arrival schedule, cbr_flag = 1) and
reports CPB underflow / overflow.

Under --field-encode each coded field is its own access unit, so the checks run
at field granularity: a frame level buffer model provably misses underflow when
the first field of a pair is large.

Model:
- AU sizes are stream bytes between consecutive AU first-NAL start codes.
- Removal times: tr(n) = bp_tr + t_c * cpb_removal_delay(n), where bp_tr is the
  removal time of the last buffering_period AU (tr = init_delay/90000 for the
  first).  Across a mid-stream buffering period the tick count continues:
  tr(bp) = tr(prev) + t_c * (delay(prev) - delay(prev-1)), which is exact for
  constant frame rate streams.
- Arrival (cbr_flag = 1): continuous flow at bit_rate from t = 0.

Limitations, sufficient for x264 output: first HRD CPB only, CBR arrival
schedule only (--nal-hrd cbr), and slice parsing assumes nothing beyond POC
type 0 or 2, which is all x264 emits.

Usage: tools/check_hrd.py stream.264 [-v]
Exit code 0 = compliant, 1 = violations found, 2 = parse/usage error.
"""

import sys

SEI_BUFFERING_PERIOD = 0
SEI_PIC_TIMING = 1

NAL_SLICE_NONIDR = 1
NAL_SLICE_DPA = 2
NAL_SLICE_IDR = 5
NAL_SEI = 6
NAL_SPS = 7
NAL_PPS = 8


class BitReader:
    def __init__(self, data):
        self.d = data
        self.pos = 0

    def u(self, n):
        v = 0
        for _ in range(n):
            byte = self.d[self.pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def ue(self):
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("ue overflow")
        return (1 << zeros) - 1 + (self.u(zeros) if zeros else 0)

    def se(self):
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)


def ebsp_to_rbsp(data):
    out = bytearray()
    zeros = 0
    for b in data:
        if zeros == 2 and b == 3:
            zeros = 0
            continue
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


def split_nals(data):
    """Yield dicts with nal_ref_idc, type, rbsp and the stream byte range
    [byte_start, byte_end) including the start code but not trailing zeroes."""
    i = 0
    starts = []
    while i + 3 <= len(data):
        if data[i] == 0 and data[i+1] == 0 and data[i+2] == 1:
            starts.append((i, 3))
            i += 3
        elif (i + 4 <= len(data) and data[i] == 0 and data[i+1] == 0
              and data[i+2] == 0 and data[i+3] == 1):
            starts.append((i, 4))
            i += 4
        else:
            i += 1
    for n, (s, off) in enumerate(starts):
        start = s + off
        end = starts[n+1][0] if n + 1 < len(starts) else len(data)
        while end > start and data[end-1] == 0:
            end -= 1
        if end <= start:
            continue
        header = data[start]
        yield {"ref_idc": (header >> 5) & 3, "type": header & 0x1F,
               "rbsp": ebsp_to_rbsp(data[start+1:end]),
               "byte_start": s, "byte_end": end}


def parse_hrd(r):
    hrd = {}
    hrd["cpb_cnt_minus1"] = r.ue()
    hrd["bit_rate_scale"] = r.u(4)
    hrd["cpb_size_scale"] = r.u(4)
    cnt = hrd["cpb_cnt_minus1"] + 1
    hrd["bit_rate_value_minus1"] = [r.ue() for _ in range(cnt)]
    hrd["cpb_size_value_minus1"] = [r.ue() for _ in range(cnt)]
    hrd["cbr_flag"] = [r.u(1) for _ in range(cnt)]
    hrd["initial_cpb_removal_delay_length_minus1"] = r.u(5)
    hrd["cpb_removal_delay_length_minus1"] = r.u(5)
    hrd["dpb_output_delay_length_minus1"] = r.u(5)
    hrd["time_offset_length"] = r.u(5)
    return hrd


def parse_sps(rbsp):
    r = BitReader(rbsp)
    sps = {}
    sps["profile_idc"] = r.u(8)
    r.u(8)  # constraint flags + reserved
    sps["level_idc"] = r.u(8)
    sps["id"] = r.ue()
    if sps["profile_idc"] in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma_format_idc = r.ue()
        if chroma_format_idc == 3:
            r.u(1)
        r.ue(); r.ue()
        r.u(1)
        if r.u(1):  # seq_scaling_matrix_present_flag
            for i in range(8 if chroma_format_idc != 3 else 12):
                if r.u(1):
                    size = 16 if i < 6 else 64
                    last = next_ = 8
                    for _ in range(size):
                        if next_:
                            next_ = (last + r.se() + 256) % 256
                        last = next_ if next_ else last
    sps["log2_max_frame_num_minus4"] = r.ue()
    sps["pic_order_cnt_type"] = r.ue()
    if sps["pic_order_cnt_type"] == 0:
        sps["log2_max_pic_order_cnt_lsb_minus4"] = r.ue()
    elif sps["pic_order_cnt_type"] == 1:
        r.u(1); r.se(); r.se()
        for _ in range(r.ue()):
            r.se()
    sps["max_num_ref_frames"] = r.ue()
    r.u(1)  # gaps_in_frame_num_value_allowed_flag
    r.ue(); r.ue()  # pic_width/height_in_map_units_minus1
    sps["frame_mbs_only"] = r.u(1)
    if not sps["frame_mbs_only"]:
        r.u(1)  # mb_adaptive_frame_field_flag
    r.u(1)  # direct_8x8_inference_flag
    if r.u(1):  # frame_cropping_flag
        r.ue(); r.ue(); r.ue(); r.ue()
    sps["vui"] = {}
    if r.u(1):  # vui_parameters_present_flag
        v = sps["vui"]
        if r.u(1):  # aspect_ratio_info_present_flag
            ar = r.u(8)
            if ar == 255:
                r.u(16); r.u(16)
        if r.u(1):  # overscan_info_present_flag
            r.u(1)
        if r.u(1):  # video_signal_type_present_flag
            r.u(3); r.u(1)
            if r.u(1):
                r.u(8); r.u(8); r.u(8)
        if r.u(1):  # chroma_loc_info_present_flag
            r.ue(); r.ue()
        v["timing"] = None
        if r.u(1):  # timing_info_present_flag
            v["timing"] = {"num_units_in_tick": r.u(32), "time_scale": r.u(32),
                           "fixed_frame_rate_flag": r.u(1)}
        v["nal_hrd"] = parse_hrd(r) if r.u(1) else None
        v["vcl_hrd"] = parse_hrd(r) if r.u(1) else None
        if v["nal_hrd"] or v["vcl_hrd"]:
            r.u(1)  # low_delay_hrd_flag
        v["pic_struct_present"] = r.u(1)
        if r.u(1):  # bitstream_restriction_flag
            r.u(1); r.ue(); r.ue(); r.ue(); r.ue(); r.ue(); r.ue()
    return sps


def parse_pps(rbsp):
    r = BitReader(rbsp)
    return {"id": r.ue(), "sps_id": r.ue()}


def parse_sei(rbsp):
    r = BitReader(rbsp)
    msgs = []
    while r.pos + 16 <= len(rbsp) * 8:
        ptype = 0
        while True:
            b = r.u(8)
            ptype += b
            if b != 0xFF:
                break
        psize = 0
        while True:
            b = r.u(8)
            psize += b
            if b != 0xFF:
                break
        payload = bytes(r.u(8) for _ in range(psize))
        msgs.append((ptype, payload))
        if r.pos >= len(rbsp) * 8 - 8:  # only rbsp_trailing_bits left
            break
    return msgs


def parse_slice_header(nal_type, nal_ref_idc, rbsp, sps_by_pps, sps_list):
    r = BitReader(rbsp)
    sh = {"nal_ref_idc": nal_ref_idc, "idr": nal_type == NAL_SLICE_IDR}
    sh["first_mb_in_slice"] = r.ue()
    sh["slice_type"] = r.ue()
    pps_id = r.ue()
    sps = sps_list[sps_by_pps[pps_id]]
    sh["frame_num"] = r.u(sps["log2_max_frame_num_minus4"] + 4)
    sh["field_pic_flag"] = 0
    sh["bottom_field_flag"] = 0
    if not sps["frame_mbs_only"]:
        sh["field_pic_flag"] = r.u(1)
        if sh["field_pic_flag"]:
            sh["bottom_field_flag"] = r.u(1)
    if sh["idr"]:
        sh["idr_pic_id"] = r.ue()
    sh["pic_order_cnt_lsb"] = 0
    if sps["pic_order_cnt_type"] == 0:
        sh["pic_order_cnt_lsb"] = r.u(sps["log2_max_pic_order_cnt_lsb_minus4"] + 4)
    return sh


def simulate(stream_path, verbose=False):
    data = open(stream_path, "rb").read()
    sps_list = {}
    sps_by_pps = {}
    aus = []

    # ---- pass 1: collect NALs and group them into access units.  This is a
    # subset of 7.4.1.2.3 that covers everything x264 emits: bottom_field_flag
    # differs between the two fields of a pair, frame_num between pairs. ----
    def au_key(sh):
        return (sh["frame_num"], sh["field_pic_flag"], sh["bottom_field_flag"],
                sh["idr"], sh["nal_ref_idc"] == 0, sh["pic_order_cnt_lsb"])

    cur = None
    prev_key = None
    pending_sei = []          # prefix SEIs of the next AU
    pending_start = None      # stream offset of the next AU's first prefix NAL
    for nal in split_nals(data):
        t = nal["type"]
        if t == NAL_SPS:
            s = parse_sps(nal["rbsp"])
            sps_list[s["id"]] = s
            if pending_start is None:
                pending_start = nal["byte_start"]
        elif t == NAL_PPS:
            p = parse_pps(nal["rbsp"])
            sps_by_pps[p["id"]] = p["sps_id"]
            if pending_start is None:
                pending_start = nal["byte_start"]
        elif t == NAL_SEI:
            pending_sei.extend(parse_sei(nal["rbsp"]))
            if pending_start is None:
                pending_start = nal["byte_start"]
        elif t in (10, 11, 12):   # end_of_sequence / end_of_stream / filler
            if cur is not None:   # suffix NALs belong to the preceding AU
                cur["byte_end"] = nal["byte_end"]
        elif t in (NAL_SLICE_NONIDR, NAL_SLICE_IDR, NAL_SLICE_DPA):
            if not sps_by_pps:
                raise ValueError("slice before any PPS")
            sh = parse_slice_header(t, nal["ref_idc"], nal["rbsp"], sps_by_pps, sps_list)
            key = au_key(sh)
            if sh["first_mb_in_slice"] == 0 and cur is not None and key != prev_key:
                aus.append(cur)
                cur = None
            prev_key = key
            if cur is None:
                cur = {"sei": pending_sei,
                       "byte_start": pending_start if pending_start is not None else nal["byte_start"],
                       "slice": sh}
                pending_sei = []
                pending_start = None
            cur["byte_end"] = nal["byte_end"]
    if cur is not None:
        aus.append(cur)
    if not aus:
        raise ValueError("no access units found")
    for au in aus:
        au["bits"] = (au["byte_end"] - au["byte_start"]) * 8

    # ---- HRD parameters ----
    sps = sps_list[max(sps_list)]
    vui = sps["vui"]
    hrd = vui.get("nal_hrd") or vui.get("vcl_hrd")
    if not hrd or not vui.get("timing"):
        raise ValueError("no HRD parameters / timing info in VUI")
    bit_rate = (hrd["bit_rate_value_minus1"][0] + 1) * (1 << (6 + hrd["bit_rate_scale"]))
    cpb_size = (hrd["cpb_size_value_minus1"][0] + 1) * (1 << (4 + hrd["cpb_size_scale"]))
    if not hrd["cbr_flag"][0]:
        raise ValueError("cbr_flag = 0: only the CBR arrival schedule is implemented")
    tick = vui["timing"]["num_units_in_tick"] / vui["timing"]["time_scale"]
    len_bp = hrd["initial_cpb_removal_delay_length_minus1"] + 1
    len_cpb = hrd["cpb_removal_delay_length_minus1"] + 1
    len_dpb = hrd["dpb_output_delay_length_minus1"] + 1

    # ---- pass 2: CPB simulation ----
    violations = []
    removed_bits = 0
    t0 = None               # removal time of the first access unit
    base = 0                # absolute tick count at the current buffering
                            # period's base
    prev_delay = None
    bp_abs_tick = 0
    max_fill = 0

    for n, au in enumerate(aus):
        cpb_removal_delay = None
        has_bp = False
        pic_struct = None
        init_delay = 0
        for ptype, payload in au["sei"]:
            r = BitReader(payload)
            if ptype == SEI_BUFFERING_PERIOD:
                r.ue()  # seq_parameter_set_id
                init_delay = r.u(len_bp)
                r.u(len_bp)  # initial_cpb_removal_delay_offset
                has_bp = True
            elif ptype == SEI_PIC_TIMING:
                cpb_removal_delay = r.u(len_cpb)
                r.u(len_dpb)  # dpb_output_delay
                if vui.get("pic_struct_present") and r.pos + 4 <= len(payload) * 8:
                    pic_struct = r.u(4)
        if cpb_removal_delay is None:
            violations.append("AU %d: no pic_timing SEI (CPB delays) found" % n)
            continue
        if has_bp and t0 is None:
            t0 = init_delay / 90000.0
        if t0 is None:
            violations.append("AU %d: pic_timing before any buffering_period" % n)
            continue
        # Absolute removal tick: the signalled delays count clock ticks from the
        # current buffering period's base AU; the BP AU itself still counts in
        # the old period, so a drop in the delay marks the switch of base to the
        # most recent BP AU.
        abs_tick = base + cpb_removal_delay
        if prev_delay is not None and abs_tick <= prev_delay:
            base = bp_abs_tick
            abs_tick = base + cpb_removal_delay
        if has_bp:
            bp_abs_tick = abs_tick
        if prev_delay is not None and abs_tick <= prev_delay:
            violations.append("AU %d: removal time not increasing (tick %d after %d)"
                              % (n, abs_tick, prev_delay))
        prev_delay = abs_tick
        tr = t0 + abs_tick * tick
        # CBR arrival: continuous flow at bit_rate since t = 0
        fill_before = bit_rate * tr - removed_bits
        max_fill = max(max_fill, fill_before)
        if fill_before > cpb_size + 0.5:
            violations.append("AU %d: CPB overflow: %.0f bits in %d-bit buffer at removal %.6fs"
                              % (n, fill_before, cpb_size, tr))
        if fill_before < au["bits"] - 0.5:
            violations.append("AU %d: CPB underflow: removing %d bits with only %.0f in buffer at removal %.6fs"
                              % (n, au["bits"], fill_before, tr))
        if verbose:
            sh = au["slice"] or {}
            print("AU %4d fld=%s bot=%s bits=%7d tick=%5d fill=%9.0f/%d%s%s"
                  % (n, sh.get("field_pic_flag", "?"), sh.get("bottom_field_flag", "?"),
                     au["bits"], abs_tick, fill_before, cpb_size,
                     " BP" if has_bp else "",
                     " ps=%d" % pic_struct if pic_struct is not None else ""))
        removed_bits += au["bits"]

    print("%s: %d access units, bit_rate=%d bps, cpb_size=%d bits, tick=%.3f ms, max fill=%.0f (%.1f%%)"
          % (stream_path, len(aus), bit_rate, cpb_size, tick * 1000, max_fill,
             100 * max_fill / cpb_size))
    if violations:
        for v in violations:
            print("VIOLATION:", v)
        return 1
    print("CPB check passed: no underflow/overflow at AU granularity")
    return 0


def main():
    args = sys.argv[1:]
    verbose = "-v" in args
    args = [a for a in args if a != "-v"]
    if len(args) != 1:
        print(__doc__)
        return 2
    try:
        return simulate(args[0], verbose)
    except (ValueError, IndexError, KeyError) as e:
        print("parse error: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
