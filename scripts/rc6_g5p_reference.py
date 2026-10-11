"""Emit a proposed prior-acquisition reference from the uploaded RAW artifact.

The proposal is inert. The later verifier downloads the completed Actions ZIP,
checks its actual digest/job/Source and replays all 1,202 original frames.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT=Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.rc6_g5p_frames import parse, wire


def require(value, reason):
    if not value:
        raise ValueError(reason)


def proposal(payload_root, *, source_sha, source_tree, run_id, run_attempt,
             artifact_id, artifact_digest):
    root=Path(payload_root).absolute()
    require(root.is_dir() and not root.is_symlink()
            and re.fullmatch(r"[0-9a-f]{40}",source_sha or "")
            and re.fullmatch(r"[0-9a-f]{40}",source_tree or "")
            and type(run_id) is int and run_id>0 and run_attempt==1
            and type(artifact_id) is int and artifact_id>0,
            "G5P_ACQUISITION_REFERENCE_EXACT_SOURCE_FIRST_RUN_REQUIRED")
    digest=artifact_digest.removeprefix("sha256:")
    require(re.fullmatch(r"[0-9a-f]{64}",digest or ""),
            "G5P_ACQUISITION_UPLOADED_ARTIFACT_DIGEST_REQUIRED")
    manifest=parse((root/"Horizon-sealed/manifest.json").read_bytes())
    lookup={row["relative_source"]:row for row in manifest["files"]}
    require(len(lookup)==len(manifest["files"]),
            "G5P_ACQUISITION_SEALED_RAW_DUPLICATE_PATH")
    def sealed(name):
        row=lookup[name]
        return "Horizon-sealed/"+row["capture_file"]
    paths={
        "model":"carrier/G5P.acquisition-model.json",
        "frame_index":"carrier/G5P.frame-index.json",
        "native_result":sealed("native-raw/native-horizon-result.json"),
        "frames_manifest":sealed("native-data/g5p-frames/manifest.json"),
        "Horizon":sealed("horizon-control/terminal.json"),
        "owned_fin":sealed("horizon-control/producer-owned-fin.json")}
    index=parse((root/paths["frame_index"]).read_bytes())
    require(index.get("schema")=="rc6.g5p-frame-zip-index.v1"
            and len(index.get("frames",[]))==1202
            and all((root/item["path"]).is_file() for item in index["frames"]),
            "G5P_ACQUISITION_ALL1202_STAGE_FRAMES_REQUIRED")
    references={}
    for role,path in paths.items():
        file=root/path
        require(file.is_file() and not file.is_symlink()
                and file.stat().st_nlink==1 and file.stat().st_size>0,
                "G5P_ACQUISITION_STAGE_REGULAR_BYTES_REQUIRED")
        references[role]={"path":path,"sha256":hashlib.sha256(file.read_bytes()).hexdigest()}
    model=parse((root/paths["model"]).read_bytes())
    require(model.get("schema")=="rc6.g5p-v3-acquisition-model.v1"
            and model.get("status")=="ACQUISITION_COMPLETE_NOT_CERTIFICATION"
            and model.get("source_sha")==source_sha and model.get("source_tree")==source_tree
            and model.get("run_id")==run_id and model.get("run_attempt")==1
            and model.get("G5P_GREEN_claimed") is False,
            "G5P_ACQUISITION_MODEL_IS_NONCERTIFYING_ONLY")
    return {"kind":"G5P_V3_ACQUISITION_V1",
            "artifact_id":artifact_id,"artifact_digest":"sha256:"+digest,
            "run_id":run_id,"run_attempt":1,**references}


def material_proposal(payload_root, *, source_sha, source_tree, run_id,
                      run_attempt, artifact_id, artifact_digest):
    root=Path(payload_root).absolute()
    receipt=root/"carrier/G5P.receipt.json"
    require(root.is_dir() and receipt.is_file() and not receipt.is_symlink()
            and type(run_id) is int and run_id>0 and run_attempt==1
            and type(artifact_id) is int and artifact_id>0,
            "G5P_MATERIAL_REFERENCE_EXACT_FIRST_RUN_REQUIRED")
    digest=artifact_digest.removeprefix("sha256:")
    require(re.fullmatch(r"[0-9a-f]{64}",digest or ""),
            "G5P_MATERIAL_UPLOADED_ARTIFACT_DIGEST_REQUIRED")
    raw=receipt.read_bytes()
    row=parse(raw)
    require(row.get("gate")=="G5P" and row.get("status")=="GREEN"
            and row.get("release_profile")=="PAPER_V3_ONLY"
            and row.get("source_sha")==source_sha
            and row.get("source_tree")==source_tree
            and row.get("run_id")==run_id and row.get("run_attempt")==1,
            "G5P_MATERIAL_PROPOSAL_REQUIRES_ORIGINAL_TRANSPORT_RECEIPT")
    return {"kind":"G5P_V3_MATERIAL_V1",
            "artifact_id":artifact_id,"artifact_digest":"sha256:"+digest,
            "run_id":run_id,"run_attempt":1,
            "receipts":[{"path":"carrier/G5P.receipt.json",
                         "sha256":hashlib.sha256(raw).hexdigest()}]}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-root",type=Path,required=True)
    parser.add_argument("--stage",choices=("acquire","certify"),required=True)
    parser.add_argument("--source-sha",required=True)
    parser.add_argument("--source-tree",required=True)
    parser.add_argument("--run-id",type=int,required=True)
    parser.add_argument("--run-attempt",type=int,required=True)
    parser.add_argument("--artifact-id",type=int,required=True)
    parser.add_argument("--artifact-digest",required=True)
    parser.add_argument("--json-out",type=Path,required=True)
    args=parser.parse_args(argv)
    require(os.environ.get("GITHUB_ACTIONS")=="true"
            and os.environ.get("GITHUB_REPOSITORY")=="mbalbo2023/Porota-trading"
            and not os.path.lexists(args.json_out),
            "G5P_REFERENCE_FIRST_CANONICAL_ACTIONS_OUTPUT_REQUIRED")
    builder=proposal if args.stage=="acquire" else material_proposal
    value={"schema":"rc6.g5p-reference-proposal.v1",
           "status":"REFERENCE_PROPOSAL_NOT_CERTIFICATION",
           "stage":args.stage,"reference":builder(args.payload_root,source_sha=args.source_sha,
               source_tree=args.source_tree,run_id=args.run_id,
               run_attempt=args.run_attempt,artifact_id=args.artifact_id,
               artifact_digest=args.artifact_digest),
           "G5P_GREEN":False,"G7_authorized":False,
           "DEPLOY_OWNER":"NOT_ACQUIRED","real_orders_sent":0}
    with args.json_out.open("xb") as stream:
        stream.write(wire(value));stream.flush();os.fsync(stream.fileno())
    print(json.dumps({"status":value["status"],
                      "artifact_id":args.artifact_id,"G5P_GREEN":False}),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
