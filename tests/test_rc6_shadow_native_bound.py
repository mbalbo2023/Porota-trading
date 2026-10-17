"""The native optimization decides size only; ordinary canonical bytes remain authority."""
import gc
import importlib.util
import json
from pathlib import Path
import random
import subprocess
import sys
import sysconfig

import pytest
from rc6_shadow_runtime import persistence as p

@pytest.fixture(scope="module")
def native(tmp_path_factory):
    if p._native_json_bound is not None:
        return p._native_json_bound
    folder=tmp_path_factory.mktemp("native-json-bound")
    binary=folder/"_rc6_json_bound.so"
    source=Path(p.__file__).with_name("_rc6_json_bound.c")
    subprocess.run(["cc","-O3","-DNDEBUG","-nostdlib","-shared","-fPIC",
        "-I"+sysconfig.get_path("include"),str(source),"-o",str(binary)],check=True,timeout=60)
    spec=importlib.util.spec_from_file_location("_rc6_json_bound",binary)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def outcome(call):
    try:return ("VALUE",call())
    except Exception as error:return ("ERROR",type(error).__name__)

def blocks(value,checker):
    from unittest.mock import patch
    encoder=json.JSONEncoder(sort_keys=True,separators=(",",":"),default=str,allow_nan=False)
    with patch.object(p,"_small_json_tree",checker):
        result=list(p._canonical_chunks(encoder,value))
    assert all(len(x)<=65536 for x in result)
    return b"".join(result)

def corpus():
    rng=random.Random(603)
    atoms=[None,True,False,0,-1,2**100,1.25,-0.0,"","ASCII","á漢字😀","\\\"\n\t\x00\x7f","\ud800"]
    def tree(depth=0):
        if depth>=4 or rng.randrange(3)==0:return rng.choice(atoms)
        kind=rng.randrange(3);size=rng.randrange(8)
        if kind==0:return [tree(depth+1) for _ in range(size)]
        if kind==1:return tuple(tree(depth+1) for _ in range(size))
        return {str(n)+rng.choice(["","á","\\\""]):tree(depth+1) for n in range(size)}
    return atoms+[tree() for _ in range(2000)]

def test_native_predicate_matches_python_for_generated_json_and_custom_bounds(native):
    cases=0
    for value in corpus():
        for maximum,nodes in ((65536,4096),(64,16),(0,0),(16,4096),(65536,1)):
            assert outcome(lambda:p._small_json_tree_python(value,maximum,nodes))==outcome(
                lambda:native.is_small(value,p.FrozenJSONDict,maximum,nodes))
            cases+=1
    assert cases==10065

@pytest.mark.parametrize("length",[0,1,255,256,257,4096])
def test_container_width_node_limit_and_canonical_stream(native,length):
    for value in (list(range(length)),{str(n):n for n in range(length)}):
        assert native.is_small(value,p.FrozenJSONDict)==p._small_json_tree_python(value)
        assert outcome(lambda:blocks(value,lambda v:native.is_small(v,p.FrozenJSONDict)))==outcome(
            lambda:blocks(value,p._small_json_tree_python))

def test_unicode_controls_escaping_and_exact_byte_bound(native):
    values=["á漢字😀",chr(127),"\ud800","\\\"\n\r\t\b\f\x00","😀"*12000,"x"*65536]
    for value in values:
        for bound in (0,1,2,3,16,65536):
            assert native.is_small(value,p.FrozenJSONDict,bound,4096)==p._small_json_tree_python(value,bound,4096)
        assert blocks(value,lambda v:native.is_small(v,p.FrozenJSONDict))==blocks(value,p._small_json_tree_python)

def test_cycle_and_very_deep_inputs_fail_the_same_way(native):
    cycle=[];cycle.append(cycle)
    nested=0
    for _ in range(3000):nested=[nested]
    for value in (cycle,nested):
        assert native.is_small(value,p.FrozenJSONDict)==p._small_json_tree_python(value)
        assert outcome(lambda:blocks(value,lambda v:native.is_small(v,p.FrozenJSONDict)))==outcome(
            lambda:blocks(value,p._small_json_tree_python))

def test_aliases_subclasses_float_and_integer_errors_preserve_encoding(native):
    class D(dict):pass
    class L(list):pass
    class S(str):pass
    alias={"a":[1,2]}
    for value in ([alias,alias],D(a=1),L([1]),S("s"),float("nan"),float("inf"),1<<100000):
        assert native.is_small(value,p.FrozenJSONDict)==p._small_json_tree_python(value)
        assert outcome(lambda:blocks(value,lambda v:native.is_small(v,p.FrozenJSONDict)))==outcome(
            lambda:blocks(value,p._small_json_tree_python))

def test_all_generated_streams_match_actual_stdlib_json(native):
    for value in corpus()[:200]:
        expected=outcome(lambda:json.dumps(value,sort_keys=True,separators=(",",":"),
            default=str,allow_nan=False).encode())
        assert outcome(lambda:blocks(value,lambda v:native.is_small(v,p.FrozenJSONDict)))==expected

def test_constructor_encoding_hint_never_becomes_content_authority(native):
    value=p.FrozenJSONDict({"a":"x","b":[]})
    object.__setattr__(value,"_canonical_json","WRONG_ENCODING_HINT")
    assert blocks(value,lambda v:native.is_small(v,p.FrozenJSONDict))==b'{"a":"x","b":[]}'
    object.__delattr__(value,"_canonical_json")
    assert outcome(lambda:native.is_small(value,p.FrozenJSONDict))==outcome(
        lambda:p._small_json_tree_python(value))

def test_native_iteration_has_no_retained_input_references(native):
    value={"a":["b",1]}
    before=sys.getrefcount(value)
    for _ in range(10000):native.is_small(value,p.FrozenJSONDict)
    gc.collect()
    assert sys.getrefcount(value)==before

def test_wrapper_uses_native_only_for_the_original_default_contract(native,monkeypatch):
    class Spy:
        calls=0
        def is_small(self,*args):
            self.calls+=1
            return native.is_small(*args)
    spy=Spy();monkeypatch.setattr(p,"_native_json_bound",spy)
    assert p._small_json_tree({"a":1}) is True and spy.calls==1
    for maximum,nodes in ((2**100,4096),(64.5,16),(65536,True),(64,16)):
        value={"a":1}
        assert p._small_json_tree(value,maximum,nodes)==p._small_json_tree_python(value,maximum,nodes)
    assert spy.calls==1

def test_source_only_fallback_has_the_original_failure_semantics(monkeypatch):
    monkeypatch.setattr(p,"_native_json_bound",None)
    for value in corpus()[:100]:
        assert outcome(lambda:p._small_json_tree(value))==outcome(lambda:p._small_json_tree_python(value))
