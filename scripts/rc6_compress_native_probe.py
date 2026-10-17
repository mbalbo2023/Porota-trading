#!/usr/bin/env python3
"""Bounded, lossless gzip RCA. Input bytes never leave this process."""
import argparse, gzip, hashlib, io, json, os, pathlib, re, resource, statistics, time

class BoundedBuffer(io.BytesIO):
    def write(self,value):
        if self.tell()+len(value)>32*1024**2: raise ValueError("RCA_COMPRESSED_OUTPUT_OVER_32MIB")
        return super().write(value)

def check(ok,code):
    if not ok: raise ValueError(code)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--input",required=True)
    parser.add_argument("--expected-sha256",required=True)
    parser.add_argument("--raw-bytes",required=True,type=int)
    parser.add_argument("--context",choices=("CI_SYNTHETIC_SELF_CHECK","READ_ONLY_NATIVE_COMPRESSION_RCA"),required=True)
    args=parser.parse_args()
    result={"schema":"rc6.bounded-native-compression-rca.v1","status":"RED","context":args.context,
            "raw_sha256":args.expected_sha256,"raw_bytes":args.raw_bytes,
            "measurements":[],"native_acceptance":False,"full_session_stress":False,
            "production_input_read_only":args.context=="READ_ONLY_NATIVE_COMPRESSION_RCA",
            "network":"NONE","provider_requests":0,"real_orders_sent":0,
            "payload_exported":False,"threads":1}
    try:
        os.nice(19)
        result["nice"]=os.getpriority(os.PRIO_PROCESS,0)
        check(result["nice"]==19,"RCA_NICE_NOT_LOWEST_PRIORITY")
        check(re.fullmatch(r"[0-9a-f]{64}",args.expected_sha256) is not None
              and 0<args.raw_bytes<=512*1024**2,"RCA_INPUT_IDENTITY_INVALID")
        from isal import igzip
        from importlib.metadata import version
        result["isal_version"]=version("isal")
        check(result["isal_version"]=="1.8.0","RCA_WHEEL_VERSION_INVALID")
        deadline=time.monotonic()+540
        for variant in ("stdlib1","isal0","isal1","isal1","isal0","stdlib1"):
            output=BoundedBuffer()
            provider=gzip if variant=="stdlib1" else igzip
            level=1 if variant=="stdlib1" else int(variant[-1])
            raw=hashlib.sha256();size=0;decode_cpu=hash_cpu=compress_cpu=0.
            total_cpu=time.process_time();started=time.monotonic()
            mark=time.process_time()
            sink=provider.GzipFile(filename="",mode="wb",compresslevel=level,fileobj=output,mtime=0)
            compress_cpu+=time.process_time()-mark
            with gzip.open(args.input,"rb") as source:
                while True:
                    check(time.monotonic()<deadline,"RCA_SELF_TIMEOUT")
                    mark=time.process_time();block=source.read(65536);decode_cpu+=time.process_time()-mark
                    if not block:break
                    size+=len(block);check(size<=args.raw_bytes,"RCA_RAW_LIMIT")
                    mark=time.process_time();raw.update(block);hash_cpu+=time.process_time()-mark
                    mark=time.process_time();sink.write(block);compress_cpu+=time.process_time()-mark
            mark=time.process_time();sink.close();compress_cpu+=time.process_time()-mark
            total_cpu=time.process_time()-total_cpu;wall=time.monotonic()-started
            check(size==args.raw_bytes and raw.hexdigest()==args.expected_sha256,"RCA_SOURCE_HASH_MISMATCH")
            compressed_bytes=output.tell();output.seek(0)
            verified=hashlib.sha256();verified_size=0;mark=time.process_time()
            with gzip.GzipFile(fileobj=output,mode="rb") as reader:
                while block:=reader.read(65536):
                    verified_size+=len(block);check(verified_size<=args.raw_bytes,"RCA_VERIFY_RAW_LIMIT")
                    verified.update(block)
            readback_cpu=time.process_time()-mark
            check(verified_size==size and verified.hexdigest()==raw.hexdigest(),"RCA_STDLIB_READBACK_MISMATCH")
            peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            check(peak<=128*1024,"RCA_RSS_OVER_128MIB")
            result["measurements"].append({"variant":variant,"raw_bytes":size,"raw_sha256":raw.hexdigest(),
                "compressed_bytes":compressed_bytes,"compression_cpu_seconds":round(compress_cpu,4),
                "source_decode_cpu_seconds":round(decode_cpu,4),"raw_hash_cpu_seconds":round(hash_cpu,4),
                "total_write_cpu_seconds":round(total_cpu,4),"write_wall_seconds":round(wall,4),
                "stdlib_readback_cpu_seconds":round(readback_cpu,4),"peak_rss_kib":peak})
            output.close()
        # Future accelerated readers must also read this exact existing standard gzip input.
        digest=hashlib.sha256();size=0
        with igzip.open(args.input,"rb") as source:
            while block:=source.read(65536):
                check(time.monotonic()<deadline,"RCA_SELF_TIMEOUT")
                size+=len(block);check(size<=args.raw_bytes,"RCA_LEGACY_RAW_LIMIT");digest.update(block)
        check(size==args.raw_bytes and digest.hexdigest()==args.expected_sha256,"RCA_LEGACY_READER_MISMATCH")
        check(len(result["measurements"])==6,"RCA_EMPTY_MEASUREMENTS")
        result["median_compression_cpu_seconds"]={variant:statistics.median(
            r["compression_cpu_seconds"] for r in result["measurements"] if r["variant"]==variant)
            for variant in ("stdlib1","isal0","isal1")}
        result["median_total_write_cpu_seconds"]={variant:statistics.median(
            r["total_write_cpu_seconds"] for r in result["measurements"] if r["variant"]==variant)
            for variant in ("stdlib1","isal0","isal1")}
        result.update(status="GREEN",legacy_reader_verified=True,stdlib_reader_verified=True)
    except Exception as error:
        result["error_class"]=type(error).__name__
        value=str(error)
        result["error_code"]=value if type(error) is ValueError and re.fullmatch(r"[A-Z_0-9]{1,80}",value) else None
    pathlib.Path("/work/metrics.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    return 0 if result["status"]=="GREEN" else 2

if __name__=="__main__":raise SystemExit(main())
