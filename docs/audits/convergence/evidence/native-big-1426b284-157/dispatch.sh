set -e
umask 077
RC6_BIG_REPO=/workspace/porota_rc6_convergence
RC6_BIG_S=1426b2844c06b164aa08b765e01676f19845a192
RC6_BIG_T=983f5a1d3f38edf671eab7d5420bbffdc93ffea2
RC6_BIG_SOURCE=/workspace/rc6-canonical-1426b284-big-20261005-source
RC6_BIG_PIN_DIR=/workspace/rc6-canonical-1426b284-big-20261005-source-raw
RC6_BIG_RAW=/workspace/rc6-canonical-1426b284-big-20261005-raw
RC6_BIG_DATA=/workspace/rc6-canonical-1426b284-big-20261005-data
RC6_BIG_WRAPPER=/workspace/rc6-canonical-1426b284-big-20261005-wrapper.py
RC6_BIG_DISPATCH=/workspace/rc6-canonical-1426b284-big-20261005-dispatch
/workspace/venv_rc6_frozen311/bin/python -I -B /tmp/rc6_export_whole_git_source.py --repo "$RC6_BIG_REPO" --sha "$RC6_BIG_S" --source "$RC6_BIG_SOURCE" --raw "$RC6_BIG_PIN_DIR" >"$RC6_BIG_DISPATCH/export.stdout" 2>"$RC6_BIG_DISPATCH/export.stderr"
/workspace/venv_rc6_frozen311/bin/python -I -B /workspace/rc6_prepare_native_big_wrapper_v2.py --source "$RC6_BIG_SOURCE" --source-repo "$RC6_BIG_REPO" --index "$RC6_BIG_PIN_DIR/source.index.json" --raw "$RC6_BIG_RAW" --data "$RC6_BIG_DATA" --out "$RC6_BIG_WRAPPER" --sha "$RC6_BIG_S" --tree "$RC6_BIG_T" >"$RC6_BIG_DISPATCH/generator.stdout" 2>"$RC6_BIG_DISPATCH/generator.stderr"
/workspace/venv_rc6_frozen311/bin/python -I -B -u "$RC6_BIG_WRAPPER" >"$RC6_BIG_DISPATCH/wrapper.stdout" 2>"$RC6_BIG_DISPATCH/wrapper.stderr"
