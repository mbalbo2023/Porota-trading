#!/usr/bin/env python3
"""Read one committed gzip row by row; export only counts, sizes and hashes."""
import argparse,gzip,hashlib,json,math,os,pathlib,re,resource,sys,time,zlib
def require(ok,code):
    if not ok:raise ValueError(code)
class Counted:
    def __init__(self,source,expected,maximum,deadline):
        self.source=source;self.expected=expected;self.maximum=maximum;self.deadline=deadline
        self.hash=hashlib.sha256();self.size=0
    def read(self,size=65536):
        require(time.monotonic()<self.deadline,"RCA_SELF_TIMEOUT")
        if size==0:return b""
        block=self.source.read(min(size if size>0 else 65536,65536))
        self.size+=len(block);require(self.size<=self.maximum,"RCA_RAW_INPUT_LIMIT")
        self.hash.update(block);return block
    def verified(self):
        require(self.size==self.maximum and self.hash.hexdigest()==self.expected,"RCA_SOURCE_HASH_MISMATCH")
def main():
    parser=argparse.ArgumentParser();parser.add_argument("--input",required=True)
    parser.add_argument("--expected-sha256",required=True);parser.add_argument("--raw-bytes",required=True,type=int)
    parser.add_argument("--context",choices=("CI_SYNTHETIC_SELF_CHECK","READ_ONLY_NATIVE_ROW_STRUCTURE_RCA"),required=True)
    args=parser.parse_args()
    result={"schema":"rc6.bounded-native-row-shape-rca.v1","status":"RED","context":args.context,
       "raw_sha256":args.expected_sha256,"raw_bytes":args.raw_bytes,"rows":[],
       "native_acceptance":False,"full_session_stress":False,"payload_exported":False,
       "production_input_read_only":args.context=="READ_ONLY_NATIVE_ROW_STRUCTURE_RCA",
       "network":"NONE","provider_requests":0,"real_orders_sent":0,"threads":1}
    try:
        os.nice(19);result["nice"]=os.getpriority(os.PRIO_PROCESS,0)
        require(result["nice"]==19,"RCA_NICE_INVALID")
        require(re.fullmatch("[0-9a-f]{64}",args.expected_sha256) is not None
             and 0<args.raw_bytes<=512*1024**2,"RCA_INPUT_IDENTITY_INVALID")
        sys.path.insert(0,"/app")
        from scripts.porota_image_source_identity import verify_source_identity
        verify_source_identity("5cb663231b52aee566dec0b3bf9c5d45684b98f5","2f1825afed2417e4d4d7a8f003fecc009708921f")
        import ijson
        import ijson.backends.yajl2_c as parser_backend
        require(ijson.__version__=="3.4.0.post0","RCA_PARSER_VERSION_INVALID")
        result["parser_version"]=ijson.__version__;result["parser_backend"]="yajl2_c"
        deadline=time.monotonic()+540
        for engine in ("SCALPING","EQUITY_SPOT"):
            started=time.monotonic();cpu=time.process_time()
            hasher=hashlib.sha256();compressor=zlib.compressobj(level=1)
            count=raw_size=zbytes=max_row=0
            def emit(raw):
                nonlocal raw_size,zbytes
                raw_size+=len(raw);require(raw_size<=512*1024**2,"RCA_ROW_ARRAY_LIMIT")
                hasher.update(raw)
                for offset in range(0,len(raw),65536):zbytes+=len(compressor.compress(raw[offset:offset+65536]))
            emit(b"[")
            with gzip.open(args.input,"rb") as source:
                reader=Counted(source,args.expected_sha256,args.raw_bytes,deadline)
                for row in parser_backend.items(reader,"frozen."+engine+".payload.rows.item",use_float=True,buf_size=65536):
                    require(type(row) is dict and count<20000,"RCA_ROW_SHAPE_INVALID")
                    encoded=json.dumps(row,sort_keys=True,separators=(",",":"),default=str,allow_nan=False).encode("ascii")
                    require(len(encoded)<=2*1024**2,"RCA_SINGLE_ROW_LIMIT")
                    if count:emit(b",")
                    emit(encoded);count+=1;max_row=max(max_row,len(encoded))
                    require(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss<=64*1024,"RCA_RSS_OVER_64MIB")
                reader.verified()
            emit(b"]");zbytes+=len(compressor.flush())
            require(count>0,"RCA_EMPTY_ROW_ARRAY")
            # Upper bound on Python bytes-piece accounting for any <=64KiB
            # capture schedule; identity sharing itself is absent from JSON.
            charged_upper=zbytes+512+(2*math.ceil(raw_size/65536)+4)*49
            result["rows"].append({"engine":engine,"count":count,
               "canonical_array_sha256":hasher.hexdigest(),"canonical_array_bytes":raw_size,
               "compressed_content_bytes":zbytes,"charged_cache_upper_bound_bytes":charged_upper,
               "max_row_bytes":max_row,"raw_32m_fit":raw_size<=32*1024**2,
               "compressed_4m_upper_bound_fit":charged_upper<=4*1024**2,
               "cpu_seconds":round(time.process_time()-cpu,4),
               "wall_seconds":round(time.monotonic()-started,4),
               "peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})
        first,second=result["rows"]
        result["normalized_frozen_arrays_equal"]=(first["count"],first["canonical_array_bytes"],first["canonical_array_sha256"])==(second["count"],second["canonical_array_bytes"],second["canonical_array_sha256"])
        result["runtime_identity_sharing"]="NOT_OBSERVABLE_FROM_SERIALIZED_JSON"
        result["raw_source_hash_verified_each_pass"]=True
        result["status"]="GREEN"
    except Exception as error:
        cls=type(error).__name__
        result["error_class"]=cls if cls in {"ValueError","TypeError","OverflowError","IncompleteJSONError","JSONError","MemoryError","ModuleNotFoundError"} else "RCA_UNAVAILABLE"
        code=str(error)
        result["error_code"]=code if type(error) is ValueError and re.fullmatch("RCA_[A-Z_0-9]{1,75}",code) else None
    pathlib.Path("/work/metrics.json").write_text(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+"\n")
    return 0 if result["status"]=="GREEN" else 2
if __name__=="__main__":raise SystemExit(main())
