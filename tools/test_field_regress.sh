#!/bin/bash
# Prove that the field encoding fixes did not change progressive or MBAFF output.
#
# Encodes a configuration matrix (tools/field_matrix.sh) twice -- once with a
# reference build of an older commit, once with the build under test -- and
# compares the two streams at NAL granularity with tools/nal_diff.py.
#
# Progressive and MBAFF rows must come out with identical coded data.  Only the
# SEI may differ: it carries X264_BUILD and the option string, both of which the
# fixes deliberately changed.  --field-encode rows are expected to differ; they
# are run to show they still encode, and to report which of them moved.
#
# Usage:
#   tools/test_field_regress.sh [regress|encode|clip]
#
#   regress   (default) reference build vs build under test over the matrix
#   encode    encode the matrix with the build under test only (crash smoke)
#   clip      just (re)generate the test clip and stop
#
# Environment:
#   X264             binary under test              (default: ./x264)
#   REF              git ref to build as reference  (default: the commit before
#                                                    the field encoding fixes)
#   REF_X264         prebuilt reference binary; skips the worktree build
#   CONFIGURE_FLAGS  flags for the reference build.  MUST match how the binary
#                    under test was configured or the comparison is meaningless;
#                    --bit-depth and --chroma-format especially.  What can be
#                    cross-checked from x264 --version is checked.
#   WORKDIR          scratch directory              (default: /tmp/x264-field-regress)
#   CLIP, CLIP_RES   external raw 4:2:0 clip instead of the synthetic one
#   FRAMES           frames to encode               (default: 60)
#   KEEP             1 to keep every stream, not just the failing ones
#
# Needs python3, and unless REF_X264 is given a compiler and make.
# Exits 0 if every prog/mbaff row is unchanged and every row encoded.

set -u

cd "$(dirname "$0")/.."
REPO=$PWD

# shellcheck source=field_matrix.sh
. "$REPO/tools/field_matrix.sh"

X264=${X264:-./x264}
REF=${REF:-21d6643f}
WORKDIR=${WORKDIR:-/tmp/x264-field-regress}
FRAMES=${FRAMES:-60}
KEEP=${KEEP:-0}

WIDTH=352
HEIGHT=288
if [ -n "${CLIP_RES:-}" ]; then
    WIDTH=${CLIP_RES%x*}
    HEIGHT=${CLIP_RES#*x}
fi

PASS=0
FAIL=0
SKIP=0
CHANGED=0
ok()   { PASS=$((PASS+1)); echo "PASS: $*"; }
bad()  { FAIL=$((FAIL+1)); echo "FAIL: $*"; }
skip() { SKIP=$((SKIP+1)); echo "SKIP: $*"; }
note() { echo "note: $*"; }
die()  { echo "ERROR: $*" >&2; exit 2; }

command -v python3 >/dev/null || die "python3 not found in PATH"
mkdir -p "$WORKDIR" || die "cannot create $WORKDIR"

make_clip() {
    if [ -n "${CLIP:-}" ]; then
        [ -f "$CLIP" ] || die "CLIP not found: $CLIP"
        return 0
    fi
    CLIP=$WORKDIR/clip_${WIDTH}x${HEIGHT}_${FRAMES}.yuv
    [ -f "$CLIP" ] && return 0
    python3 "$REPO/tools/make_test_clip.py" "$CLIP" "$WIDTH" "$HEIGHT" "$FRAMES" \
        || die "clip generation failed"
    note "generated $CLIP"
}

build_reference() {
    if [ -n "${REF_X264:-}" ]; then
        [ -x "$REF_X264" ] || die "REF_X264 is not executable: $REF_X264"
        echo "$REF_X264"
        return 0
    fi
    local dir=$WORKDIR/reference stamp=$WORKDIR/reference.stamp sha jobs
    sha=$(git rev-parse --verify "$REF^{commit}" 2>/dev/null) || die "unknown ref: $REF"
    if [ -x "$dir/x264" ] && [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$sha ${CONFIGURE_FLAGS:-}" ]; then
        note "reference binary (cached): $dir/x264 @ $REF" >&2
        echo "$dir/x264"
        return 0
    fi
    note "building reference @ $REF in $dir (one-off)" >&2
    rm -rf "$dir"
    git worktree prune >&2
    git worktree add --detach "$dir" "$sha" >&2 || die "git worktree add failed"
    ( cd "$dir" && ./configure ${CONFIGURE_FLAGS:-} >"$WORKDIR/reference.configure.log" 2>&1 ) \
        || die "reference configure failed, see $WORKDIR/reference.configure.log"
    jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
    ( cd "$dir" && make -j"$jobs" x264 >"$WORKDIR/reference.build.log" 2>&1 ) \
        || die "reference build failed, see $WORKDIR/reference.build.log"
    printf '%s %s' "$sha" "${CONFIGURE_FLAGS:-}" > "$stamp"
    echo "$dir/x264"
}

# The comparison only means anything if both binaries were built the same way.
# Bit depth and chroma format are the two that silently invalidate everything,
# and x264 --version reports both.
check_builds_match() {
    local a b
    a=$("$1" --version 2>/dev/null | grep -i "configuration\|bit depth" | tr "\n" " ")
    b=$("$2" --version 2>/dev/null | grep -i "configuration\|bit depth" | tr "\n" " ")
    if [ "$a" != "$b" ]; then
        echo "ERROR: the two builds are configured differently, comparison would be meaningless" >&2
        echo "  reference:  $a" >&2
        echo "  under test: $b" >&2
        echo "  set CONFIGURE_FLAGS to match the build under test" >&2
        exit 2
    fi
}

COMMON=(--fps 25 --keyint 24 --threads 1)

# encode BINARY OUT TAG OPTS...    ("@2pass" in OPTS makes it a two pass run)
encode() {
    local bin=$1 out=$2 tag=$3
    shift 3
    local log=$WORKDIR/$tag.log
    local stats=$WORKDIR/$tag.stats
    local twopass=0
    local opts=()
    local o
    for o in "$@"; do
        if [ "$o" = "@2pass" ]; then
            twopass=1
        else
            opts+=("$o")
        fi
    done
    set -- "$CLIP" --input-res "${WIDTH}x${HEIGHT}" --frames "$FRAMES" "${COMMON[@]}" "${opts[@]}"

    if [ $twopass = 1 ]; then
        rm -f "$stats" "$stats.mbtree"
        "$bin" "$@" --pass 1 --stats "$stats" -o /dev/null >"$log" 2>&1 || return 1
        "$bin" "$@" --pass 2 --stats "$stats" -o "$out" >>"$log" 2>&1 || return 1
    else
        "$bin" "$@" -o "$out" >"$log" 2>&1 || return 1
    fi
    [ -s "$out" ]
}

clean_row() {
    local name=$1
    rm -f "$WORKDIR/$name.ref.264" "$WORKDIR/$name.new.264"
    rm -f "$WORKDIR/$name.ref.log" "$WORKDIR/$name.new.log"
    rm -f "$WORKDIR/$name.ref.stats" "$WORKDIR/$name.ref.stats.mbtree"
    rm -f "$WORKDIR/$name.new.stats" "$WORKDIR/$name.new.stats.mbtree"
}

cmd_encode() {
    make_clip
    local entry name class opts
    for entry in "${FIELD_MATRIX[@]}"; do
        name=${entry%%|*}
        class=${entry#*|}
        class=${class%%|*}
        opts=${entry##*|}
        # shellcheck disable=SC2086
        if encode "$X264" "$WORKDIR/$name.new.264" "$name.new" $opts; then
            ok "$name ($class) encoded"
        else
            bad "$name ($class) failed to encode"
            tail -n 4 "$WORKDIR/$name.new.log" | sed "s/^/    /" >&2
        fi
        [ "$KEEP" = 1 ] || clean_row "$name"
    done
}

cmd_regress() {
    make_clip
    local ref_bin
    ref_bin=$(build_reference) || exit 2
    check_builds_match "$ref_bin" "$X264"

    echo "== reference:  $ref_bin ($REF)"
    echo "== under test: $X264"
    echo "== clip:       $CLIP (${WIDTH}x${HEIGHT}, $FRAMES frames)"
    echo

    local entry name class opts a b out verdict keep_row
    for entry in "${FIELD_MATRIX[@]}"; do
        name=${entry%%|*}
        class=${entry#*|}
        class=${class%%|*}
        opts=${entry##*|}
        a=$WORKDIR/$name.ref.264
        b=$WORKDIR/$name.new.264
        keep_row=$KEEP

        # shellcheck disable=SC2086
        if ! encode "$ref_bin" "$a" "$name.ref" $opts; then
            if [ "$class" = field ]; then
                skip "$name ($class): the reference build cannot encode it"
            else
                bad "$name ($class): reference encode failed"
                tail -n 4 "$WORKDIR/$name.ref.log" | sed "s/^/    /" >&2
            fi
            clean_row "$name"
            continue
        fi
        # shellcheck disable=SC2086
        if ! encode "$X264" "$b" "$name.new" $opts; then
            bad "$name ($class): encode under test failed"
            tail -n 4 "$WORKDIR/$name.new.log" | sed "s/^/    /" >&2
            clean_row "$name"
            continue
        fi

        out=$(python3 "$REPO/tools/nal_diff.py" "$a" "$b" 2>&1)
        verdict=$(printf "%s\n" "$out" | sed -n "s/^VERDICT: //p")
        [ -n "$verdict" ] || verdict="parse-error"

        case "$class:$verdict" in
            field:identical|field:sei-only)
                ok "$name ($class): unchanged by the fixes"
                ;;
            field:differ)
                CHANGED=$((CHANGED+1))
                ok "$name ($class): output changed, as expected"
                ;;
            *:identical)
                ok "$name ($class): byte-identical"
                ;;
            *:sei-only)
                ok "$name ($class): coded data identical, SEI differs"
                ;;
            *)
                bad "$name ($class): coded data changed"
                printf "%s\n" "$out" | sed "s/^/    /" >&2
                keep_row=1
                ;;
        esac

        [ "$keep_row" = 1 ] || clean_row "$name"
    done
}

case "${1:-regress}" in
    regress) cmd_regress ;;
    encode)  cmd_encode ;;
    clip)    make_clip; exit 0 ;;
    *)       die "unknown command: $1, try regress, encode or clip" ;;
esac

echo
echo "---"
echo "passed: $PASS, failed: $FAIL, skipped: $SKIP, field rows changed: $CHANGED"
[ "$FAIL" -eq 0 ]
