#!/usr/bin/env python3
"""Compare two Annex B H.264 streams at NAL granularity, ignoring SEI.

The version and options SEI carries X264_BUILD and the option string, so it
legitimately differs between two builds of x264 even when the coded pictures
are identical.  Everything else -- SPS, PPS, every slice, and the order and
framing of all of them -- must match exactly for a change to be a no-op.

Usage: tools/nal_diff.py a.264 b.264 [-v]
Exit code 0 = coded data identical, 1 = differs, 2 = parse/usage error.
The last line printed is one of:
    VERDICT: identical      byte-identical including SEI
    VERDICT: sei-only       identical apart from SEI payloads
    VERDICT: differ         coded data differs
"""

import sys

NAL_SEI = 6

NAL_NAMES = {1: "slice", 2: "dpa", 3: "dpb", 4: "dpc", 5: "idr", 6: "sei",
             7: "sps", 8: "pps", 9: "aud", 10: "eoseq", 11: "eostream",
             12: "filler"}


def split_nals(data):
    """Yield (nal_type, nal_ref_idc, payload) for each NAL, where payload is
    everything between this start code and the next, i.e. the NAL including its
    header byte.  Trailing zeroes are deliberately kept: x264 emits no
    trailing_zero_8bits, so any zeroes at the end of a NAL are cabac_zero_words
    and a change in how many of them there are is a real coding change."""
    starts = []
    i = 0
    n = len(data)
    while i + 3 <= n:
        if data[i] == 0 and data[i+1] == 0 and data[i+2] == 1:
            starts.append((i, 3))
            i += 3
        elif (i + 4 <= n and data[i] == 0 and data[i+1] == 0
              and data[i+2] == 0 and data[i+3] == 1):
            starts.append((i, 4))
            i += 4
        else:
            i += 1
    for k, (pos, off) in enumerate(starts):
        begin = pos + off
        end = starts[k+1][0] if k + 1 < len(starts) else n
        if end <= begin:
            continue
        yield (data[begin] & 0x1F, (data[begin] >> 5) & 3, data[begin:end])


def describe(nal, index):
    t, ref_idc, payload = nal
    return "nal %d (%s, nal_ref_idc %d, %d bytes)" % (
        index, NAL_NAMES.get(t, "type %d" % t), ref_idc, len(payload))


def hexdump(buf):
    return " ".join("%02x" % b for b in buf)


def first_byte_diff(a, b):
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i
    return min(len(a), len(b))


def main():
    args = [a for a in sys.argv[1:] if a != "-v"]
    verbose = "-v" in sys.argv[1:]
    if len(args) != 2:
        print(__doc__)
        return 2

    try:
        raw_a = open(args[0], "rb").read()
        raw_b = open(args[1], "rb").read()
    except OSError as e:
        print("read error: %s" % e)
        return 2

    if not raw_a or not raw_b:
        print("empty stream: %s" % (args[0] if not raw_a else args[1]))
        return 2

    nals_a = list(split_nals(raw_a))
    nals_b = list(split_nals(raw_b))
    if not nals_a or not nals_b:
        print("no NAL units found")
        return 2

    coded_a = [x for x in nals_a if x[0] != NAL_SEI]
    coded_b = [x for x in nals_b if x[0] != NAL_SEI]

    if verbose:
        print("%s: %d NALs (%d coded), %d bytes" % (args[0], len(nals_a), len(coded_a), len(raw_a)))
        print("%s: %d NALs (%d coded), %d bytes" % (args[1], len(nals_b), len(coded_b), len(raw_b)))

    if len(coded_a) != len(coded_b):
        print("coded NAL count differs: %d vs %d" % (len(coded_a), len(coded_b)))
        # locate the first structural divergence to make the report useful
        for i in range(min(len(coded_a), len(coded_b))):
            if coded_a[i][0] != coded_b[i][0]:
                print("  first type mismatch at %s vs %s"
                      % (describe(coded_a[i], i), describe(coded_b[i], i)))
                break
        print("VERDICT: differ")
        return 1

    for i, (na, nb) in enumerate(zip(coded_a, coded_b)):
        if na[2] != nb[2]:
            off = first_byte_diff(na[2], nb[2])
            print("coded data differs at %s" % describe(na, i))
            print("  reference: %s" % describe(na, i))
            print("  under test: %s" % describe(nb, i))
            print("  first differing byte at offset %d in the NAL" % off)
            lo = max(0, off - 4)
            print("    reference:  %s" % hexdump(na[2][lo:off+8]))
            print("    under test: %s" % hexdump(nb[2][lo:off+8]))
            print("VERDICT: differ")
            return 1

    sei_a = [x[2] for x in nals_a if x[0] == NAL_SEI]
    sei_b = [x[2] for x in nals_b if x[0] == NAL_SEI]
    if raw_a == raw_b:
        print("VERDICT: identical")
    elif sei_a == sei_b:
        # coded NALs match and SEI payloads match, so only the framing differs
        # (start code lengths or trailing zeroes) -- still not a coding change.
        print("streams differ only in start code framing")
        print("VERDICT: sei-only")
    else:
        if len(sei_a) != len(sei_b):
            print("coded data identical; SEI count differs (%d vs %d)"
                  % (len(sei_a), len(sei_b)))
        else:
            if verbose:
                for i, (sa, sb) in enumerate(zip(sei_a, sei_b)):
                    if sa != sb:
                        print("  sei %d differs (%d vs %d bytes)" % (i, len(sa), len(sb)))
            print("coded data identical; %d SEI NAL(s) differ (version/options string)"
                  % sum(1 for sa, sb in zip(sei_a, sei_b) if sa != sb))
        print("VERDICT: sei-only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
