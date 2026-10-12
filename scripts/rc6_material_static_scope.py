"""Run the original governed scope under a reviewed, locked product Python."""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo-root',required=True)
    parser.add_argument('--source-sha',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    root=Path(args.repo_root).resolve(strict=True)
    if str(root) not in sys.path:sys.path.insert(0,str(root))
    governed=runpy.run_path(str(root/'scripts/rc6_controlled_governed_runner.py'))
    arguments,exclusions,inventory=governed['approved_scope'](root,args.source_sha)
    result={'source_sha':args.source_sha,'arguments':arguments,
            'exclusions':exclusions,'inventory':inventory}
    raw=json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    output=Path(args.output)
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


if __name__=='__main__':main()
