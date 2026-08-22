# Encoder configuration matrix -- shared data, source'd by:
#   tools/test_field_regress.sh   (reference build vs build under test)
#
# Each entry is NAME|CLASS|OPTS.
#
# CLASS decides what the regression driver asserts:
#   prog    progressive coding; coded data MUST NOT change
#   mbaff   MBAFF coding;       coded data MUST NOT change
#   field   --field-encode;     coded data is EXPECTED to change (these rows are
#           run to prove they still encode, and to show which of them the fixes
#           actually moved)
#
# OPTS may contain the token @2pass, which the driver strips and turns into a
# --stats pass 1 / pass 2 run of the remaining options.
#
# The prog/mbaff rows are chosen to cover every non-field code path the field
# encoding fixes touched:
#   deblock_ref_table        weightp 2 with deblocking on
#   chroma parity offset     MBAFF with ref >= 2, B-frames, subme >= 8, chroma ME
#   lossless prediction      --qp 0, both progressive and MBAFF
#   temporal mv predictor    B-pyramid with --direct temporal
#   ref_pic_list_modification weightp 2 and 2-pass reordering
#   pic_struct auto-enable   MBAFF (and --fake-interlaced)
#   level validation         --fake-interlaced exercises sps->b_half_height
#   tff/bff parsing          --tff followed by --no-interlaced
FIELD_MATRIX=(
    "prog_crf|prog|--crf 23"
    "prog_crf_ref4_weightp2|prog|--crf 23 --ref 4 --weightp 2 --bframes 3"
    "prog_crf_weightp1|prog|--crf 23 --ref 3 --weightp 1"
    "prog_bpyr_temporal|prog|--crf 23 --bframes 5 --b-pyramid normal --direct temporal --ref 4"
    "prog_bpyr_strict_spatial|prog|--crf 23 --bframes 5 --b-pyramid strict --direct spatial --ref 3"
    "prog_subme10_chromame|prog|--crf 23 --subme 10 --ref 4 --bframes 3 --me umh --merange 32"
    "prog_lossless|prog|--qp 0 --ref 2"
    "prog_lossless_8x8dct|prog|--qp 0 --ref 2 --bframes 2 --8x8dct"
    "prog_cavlc|prog|--crf 23 --no-cabac --ref 3 --weightp 2"
    "prog_cbr_vbv|prog|--bitrate 1500 --vbv-maxrate 1500 --vbv-bufsize 1500 --threads 1"
    "prog_2pass|prog|@2pass --bitrate 1200 --ref 4 --bframes 3"
    "prog_intra_refresh|prog|--crf 23 --intra-refresh --bframes 0"
    "prog_keyint1|prog|--qp 26 --keyint 1"
    "prog_open_gop|prog|--crf 23 --open-gop --bframes 3 --ref 3"
    "prog_fake_interlaced|prog|--crf 23 --fake-interlaced --bframes 2"
    "prog_threads4|prog|--crf 23 --ref 4 --bframes 3 --threads 4"
    "prog_scenecut_in_minkeyint|prog|--crf 23 --ref 3 --bframes 3 --keyint 240 --min-keyint 60"
    "prog_sliced_threads|prog|--crf 23 --ref 3 --sliced-threads --threads 4"

    "mbaff_tff_crf|mbaff|--crf 23 --tff --ref 4 --bframes 3"
    "mbaff_bff_crf|mbaff|--crf 23 --bff --ref 4 --bframes 3"
    "mbaff_tff_weightp2|mbaff|--crf 23 --tff --ref 4 --weightp 2 --bframes 0"
    "mbaff_tff_temporal|mbaff|--crf 23 --tff --bframes 5 --b-pyramid normal --direct temporal --ref 4"
    "mbaff_tff_subme10|mbaff|--crf 23 --tff --subme 10 --ref 4 --bframes 3 --me umh --merange 32"
    "mbaff_tff_lossless|mbaff|--qp 0 --tff --ref 2"
    "mbaff_tff_cavlc|mbaff|--crf 23 --tff --no-cabac --ref 3 --weightp 2"
    "mbaff_tff_cbr_vbv|mbaff|--tff --bitrate 1500 --vbv-maxrate 1500 --vbv-bufsize 1500 --threads 1"
    "mbaff_tff_2pass|mbaff|@2pass --tff --bitrate 1200 --ref 4 --bframes 3"
    "mbaff_tff_threads4|mbaff|--crf 23 --tff --ref 4 --bframes 3 --threads 4"
    "mbaff_tff_scenecut_in_minkeyint|mbaff|--crf 23 --tff --ref 3 --bframes 3 --keyint 240 --min-keyint 60"
    "mbaff_tff_then_progressive|prog|--crf 23 --tff --no-interlaced --ref 3"

    "field_tff_crf|field|--field-encode --tff --crf 23 --ref 3"
    "field_bff_crf|field|--field-encode --bff --crf 23 --ref 3"
    "field_tff_weightp2|field|--field-encode --tff --crf 23 --ref 4 --weightp 2"
    "field_tff_weightp0|field|--field-encode --tff --crf 23 --ref 4 --weightp 0"
    "field_tff_cbr_hrd|field|--field-encode --tff --bitrate 1500 --vbv-maxrate 1500 --vbv-bufsize 1500 --nal-hrd cbr --threads 1"
    "field_tff_2pass|field|@2pass --field-encode --tff --bitrate 1200 --ref 3"
    "field_tff_intra_refresh|field|--field-encode --tff --crf 23 --intra-refresh"
    "field_tff_keyint1|field|--field-encode --tff --qp 26 --keyint 1"
    "field_tff_lossless|field|--field-encode --tff --qp 0 --ref 2"
    "field_tff_cavlc|field|--field-encode --tff --crf 23 --no-cabac --ref 3"
    # min-keyint is large enough that both of the clip's scene changes fall
    # inside it, so they become I fields that never turn into keyframes -- the
    # case where b_ref_opp_field and the frame cost window used to disagree.
    # The second one is a mid-frame cut, i.e. it lands on a second field, which
    # is what the keyframe deferral is for.
    "field_tff_scenecut_in_minkeyint|field|--field-encode --tff --crf 23 --ref 3 --keyint 240 --min-keyint 60"
    "field_bff_scenecut_in_minkeyint|field|--field-encode --bff --crf 23 --ref 3 --keyint 240 --min-keyint 60"
)
