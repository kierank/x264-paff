#!/usr/bin/env python3
"""Generate a deterministic raw 4:2:0 test clip, without needing ffmpeg.

A textured background plus rectangles moving at different speeds -- one of them
vertically by an odd number of lines per frame, so the two field parities never
see it in the same place -- and two scene changes:

  * one on a frame boundary, at frame count/3, which lands on a first field
  * one in the middle of a frame, at frame 2*count/3, whose top half-lines come
    from the old scene and bottom half-lines from the new one.  In field coding
    that puts the scene change between the two fields of a single frame, i.e. on
    a second field, which is what keyframe placement has to cope with.

The background also carries a comb between the field parities, so field and
frame coding are not equivalent even on the static part of the picture.

Usage: tools/make_test_clip.py out.yuv WIDTH HEIGHT FRAMES
"""

import sys


def build(path, w, h, n):
    cw, ch = w // 2, h // 2
    cut_frame = n // 3
    cut_mid = (2 * n) // 3

    def bg_luma(phase):
        rows = []
        for y in range(h):
            comb = 24 if (y & 1) else 0
            rows.append(bytes((((x * 13 + y * 7 + phase * 61) ^ ((x >> 2) * 5)) + comb) & 0xff
                              for x in range(w)))
        return b"".join(rows)

    def bg_chroma(phase, off):
        rows = []
        for y in range(ch):
            rows.append(bytes((((x * 9 + y * 11 + phase * 37 + off) & 0x7f) + 64) & 0xff
                              for x in range(cw)))
        return b"".join(rows)

    scenes = [(bg_luma(0), bg_chroma(0, 0), bg_chroma(0, 40)),
              (bg_luma(1), bg_chroma(1, 20), bg_chroma(1, 90)),
              (bg_luma(2), bg_chroma(2, 55), bg_chroma(2, 15))]

    # (width, height, dx, dy, luma, cb, cr); the second one moves an odd number
    # of lines per frame so the field parities never agree about where it is
    objs = [[(64, 48, 3, 1, 235, 90, 240),
             (48, 64, -2, 3, 16, 200, 60),
             (80, 32, 1, -1, 128, 128, 128)],
            [(56, 56, -3, 2, 200, 60, 180),
             (40, 72, 2, 3, 40, 170, 100),
             (96, 24, -1, -2, 90, 128, 128)],
            [(72, 40, 1, 3, 160, 220, 70),
             (32, 88, -4, 1, 60, 80, 200),
             (64, 64, 2, -3, 210, 128, 128)]]

    def blit(plane, pw, ph, x0, y0, bw, bh, val):
        bw = min(bw, pw)
        bh = min(bh, ph)
        row = bytes([val]) * bw
        x0 %= pw
        for y in range(y0, y0 + bh):
            base = (y % ph) * pw
            if x0 + bw <= pw:
                plane[base + x0: base + x0 + bw] = row
            else:
                k = pw - x0
                plane[base + x0: base + pw] = row[:k]
                plane[base: base + bw - k] = row[k:]

    def frame(f, scene):
        y = bytearray(scenes[scene][0])
        u = bytearray(scenes[scene][1])
        v = bytearray(scenes[scene][2])
        for (ow, oh, dx, dy, ly, cb, cr) in objs[scene]:
            x0 = (f * dx) % w
            y0 = (f * dy) % h
            blit(y, w, h, x0, y0, ow, oh, ly)
            blit(u, cw, ch, x0 // 2, y0 // 2, ow // 2, oh // 2, cb)
            blit(v, cw, ch, x0 // 2, y0 // 2, ow // 2, oh // 2, cr)
        return y, u, v

    def weave(a, b, pw, ph):
        """Take even lines from a and odd lines from b, i.e. cut between the
        two fields of this frame."""
        out = bytearray(a)
        for r in range(1, ph, 2):
            out[r * pw:(r + 1) * pw] = b[r * pw:(r + 1) * pw]
        return out

    with open(path, "wb") as fh:
        for f in range(n):
            if f < cut_frame:
                scene = 0
            elif f < cut_mid:
                scene = 1
            else:
                scene = 2
            if f == cut_mid:
                ya, ua, va = frame(f, 1)
                yb, ub, vb = frame(f, 2)
                y = weave(ya, yb, w, h)
                u = weave(ua, ub, cw, ch)
                v = weave(va, vb, cw, ch)
            else:
                y, u, v = frame(f, scene)
            fh.write(bytes(y))
            fh.write(bytes(u))
            fh.write(bytes(v))


def main():
    if len(sys.argv) != 5:
        print(__doc__)
        return 2
    path, w, h, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    if w % 2 or h % 2:
        print("width and height must be even")
        return 2
    if n < 9:
        print("need at least 9 frames for both scene changes")
        return 2
    build(path, w, h, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
