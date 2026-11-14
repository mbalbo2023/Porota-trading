"""Real RSA signatures and GitHub-shaped claims, no network or host mutation."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


FINGERPRINT = "ede3589a375077b3d40d6941cd82e5e6b141c615e616cdd693a046199232d1b3"
REPO = "mbalbo2023/Porota-trading"
MAIN = "b" * 40


def encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class IdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.files.cleanup)
        cls.key = Path(cls.files.name) / "fixture.pem"
        cls.cert = Path(cls.files.name) / "fixture.crt"
        cls.openssl("req", "-new", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(cls.key), "-out", str(cls.cert), "-days", "1",
                    "-subj", "/CN=fixture")
        n_line = cls.openssl("rsa", "-in", str(cls.key), "-noout", "-modulus").strip()
        modulus = bytes.fromhex(n_line.removeprefix(b"Modulus=").decode())
        der = cls.openssl("x509", "-in", str(cls.cert), "-outform", "DER")
        cls.jwk = {"kty":"RSA","use":"sig","alg":"RS256","kid":"fixture-key",
                   "n":encode(modulus),"e":"AQAB","x5c":[base64.b64encode(der).decode()]}

    @staticmethod
    def openssl(*args, data=None):
        return subprocess.run(["/usr/bin/openssl", *args], input=data, capture_output=True,
                              check=True, timeout=6).stdout

    def setUp(self):
        name = "scripts.rc6_genesis_host_identity"
        self.assertIsNotNone(importlib.util.find_spec(name), "the host has no OIDC verifier")
        self.identity = importlib.import_module(name)
        self.now = int(time.time())
        self.request = {"schema":"RC6_R367_HOST_REQUEST_V1","operation":"PREPARE_ENV",
            "step":"INITIAL","authority":{"binding":{"trusted_main_sha":MAIN,"candidate_sha":"c"*40,
            "candidate_tree":"d"*40,"host_identity_sha256":FINGERPRINT,"run_id":"8","run_attempt":"1",
            "prepare_go_id":10,"prepare_nonce":"1"*32},"actor_id":"151845499","dispatch_run_id":"9",
            "dispatch_run_attempt":"2","public_versions":{"980":{"comments":200,"updated_at":stamp(self.now-20)},"1093":{"comments":232,"updated_at":stamp(self.now-20)}},"issued_at":stamp(self.now-30),"expires_at":stamp(self.now+150)},
            "release":None}
        self.audience = "urn:porota:r367:" + hashlib.sha256(json_bytes(self.request)+b"\n").hexdigest()
        self.claims = {"iss":"https://token.actions.githubusercontent.com","aud":self.audience,
            "sub":"repo:mbalbo2023/Porota-trading:ref:refs/heads/main",
            "actor":"mbalbo2023","actor_id":"151845499","repository":REPO,"repository_id":"1338680554",
            "repository_owner":"mbalbo2023","repository_owner_id":"151845499","ref":"refs/heads/main",
            "ref_type":"branch","event_name":"workflow_dispatch","sha":MAIN,"workflow_sha":MAIN,
            "workflow_ref":REPO+"/.github/workflows/rc6-r321-root-install-approved.yml@refs/heads/main",
            "run_id":"9","run_attempt":"2","iat":self.now-4,"nbf":self.now-4,"exp":self.now+100}
        self.headers = {"alg":"RS256","kid":"fixture-key","typ":"JWT"}
        self.keys = {"keys":[deepcopy(self.jwk)]}
        self.jwks = patch.object(self.identity,"_fetch_keys",side_effect=lambda:deepcopy(self.keys))
        self.fetch = self.jwks.start()
        self.addCleanup(self.jwks.stop)

    def token(self, *, claims=None, header=None, audience=None):
        data = {**(self.claims if claims is None else claims)}
        if audience is not None:
            data["aud"]=audience
        unsigned=encode(json_bytes(self.headers if header is None else header))+"."+encode(json_bytes(data))
        return unsigned+"."+encode(self.openssl("dgst","-sha256","-sign",str(self.key),data=unsigned.encode()))

    def authorize(self, **kwargs):
        self.request["authority"]["host_identity_token"]=self.token(**kwargs)
        return self.identity.signed_authority(self.request)

    def test_real_signature_default_and_immutable_subject(self):
        for sub in (self.claims["sub"],"repo:mbalbo2023@151845499/Porota-trading@1338680554:ref:refs/heads/main"):
            with self.subTest(sub=sub),patch.object(self.identity,"_current_authority",return_value=True) as live:
                callback=self.authorize(claims={**self.claims,"sub":sub})
                self.assertTrue(callback())
                self.assertEqual(live.call_count,1)
                self.assertTrue(callback(force=True))
                self.assertEqual(live.call_count,2)

    def test_foreign_workflow_name_run_owner_and_subject_rejected(self):
        for key,value in (("run_id","8"),("run_attempt","1"),("actor_id","123"),
                          ("repository_id","134"),("repository_owner_id","99"),
                          ("sha","5"*40),("workflow_sha","5"*40),("workflow_ref","other"),
                          ("sub","repo:"+REPO+":pull_request"),("event_name","push"),
                          ("iss","https://evil.test"),("aud","urn:porota:r367:wrong"),
                          ("actor","other"),("ref","refs/heads/work")):
            with self.subTest(claim=key),patch.object(self.identity,"_current_authority",return_value=True):
                with self.assertRaises(self.identity.IdentityHold):
                    self.authorize(claims={**self.claims,key:value})

    def test_tampering_body_host_and_untrusted_kid_or_key_url_rejected(self):
        with patch.object(self.identity,"_current_authority",return_value=True):
            self.request["step"]="OTHER"
            with self.assertRaises(self.identity.IdentityHold):
                self.authorize()
            self.request["step"]="INITIAL"
            for key,value in (("alg","HS256"),("jku","https://attacker.test/keys"),
                              ("x5u","https://attacker.test/cert"),("kid","unknown")):
                with self.subTest(header=key),self.assertRaises(self.identity.IdentityHold):
                    self.authorize(header={**self.headers,key:value})
            self.keys["keys"].append(deepcopy(self.jwk))
            with self.assertRaises(self.identity.IdentityHold):
                self.authorize()

    def test_issuer_expiry_and_go_expiry_hold_before_and_after_network(self):
        for claims in ({"exp":self.now-1},{"iat":self.now+80},{"nbf":True},{"exp":"9999999999"}):
            with self.subTest(claims=claims),patch.object(self.identity,"_current_authority",return_value=True):
                with self.assertRaises(self.identity.IdentityHold):
                    self.authorize(claims={**self.claims,**claims})
        with patch.object(self.identity,"_current_authority",return_value=True):
            cb=self.authorize()
            with patch.object(self.identity.time,"time",return_value=self.now+110):
                with self.assertRaises(self.identity.IdentityHold):
                    cb()
        self.request["authority"].pop("host_identity_token",None)
        self.request["authority"]["expires_at"]=stamp(self.now-2)
        audience="urn:porota:r367:"+hashlib.sha256(json_bytes(self.request)+b"\n").hexdigest()
        with patch.object(self.identity,"_current_authority",return_value=True),self.assertRaises(self.identity.IdentityHold):
            self.authorize(audience=audience)

    def test_live_guard_error_is_closed_and_os_error_does_not_echo_token(self):
        token=self.token()
        self.request["authority"]["host_identity_token"]=token
        with patch.object(self.identity,"_current_authority",side_effect=OSError(token)):
            with self.assertRaises(self.identity.IdentityHold) as caught:
                self.identity.signed_authority(self.request)
        self.assertNotIn(token,str(caught.exception))

    def public_responses(self):
        b=self.request["authority"]["binding"]
        vers=self.request["authority"]["public_versions"]
        url=self.identity.API
        repo={"id":1338680554,"full_name":REPO}
        rows={url+"/issues/"+n:{"number":int(n),"url":url+"/issues/"+n, **data} for n,data in vers.items()}
        rows[url+"/git/ref/heads/main"]={"ref":"refs/heads/main","object":{"type":"commit","sha":MAIN}}
        rows[url+"/pulls/1087"]={"number":1087,"state":"open","draft":False,"merged":False,
             "head":{"ref":"integration/rc6-a1115-r87-unified-paper-candidate-20261110","sha":b["candidate_sha"],"repo":repo},
             "base":{"ref":"deploy/rc6-pr69-isolated-20260915","repo":repo}}
        return rows

    def test_three_apply_verbs_measure_four_gets_per_sample_and_no_get_for_a_clock_check(self):
        records=self.public_responses()
        for step in ("SEAL_ENV","PIN","INSTALL"):
            self.request["operation"]="APPLY_GENESIS"
            self.request["step"]=step
            self.request["authority"].pop("host_identity_token",None)
            aud="urn:porota:r367:"+hashlib.sha256(json_bytes(self.request)+b"\n").hexdigest()
            with patch.object(self.identity,"_fetch",side_effect=records.__getitem__) as http:
                with patch.object(self.identity.time,"monotonic",return_value=200):
                    cb=self.authorize(audience=aud)
                    for _ in range(12): self.assertTrue(cb())
                    self.assertEqual(http.call_count,4)
                    self.assertTrue(cb(force=True))
                    self.assertEqual(http.call_count,8)

    def test_expired_cache_reopens_mutated_issue_then_permanently_holds(self):
        records=self.public_responses()
        with patch.object(self.identity,"_fetch",side_effect=records.__getitem__) as http:
            with patch.object(self.identity.time,"monotonic",return_value=10):
                cb=self.authorize()
            records[self.identity.API+"/issues/980"]["comments"]+=1
            self.assertEqual(http.call_count,4)
            with patch.object(self.identity.time,"monotonic",return_value=14.01),self.assertRaises(self.identity.IdentityHold):
                cb()
            received=http.call_count
            records[self.identity.API+"/issues/980"]["comments"]-=1
            with self.assertRaises(self.identity.IdentityHold):
                cb()
            self.assertEqual(http.call_count,received)

    def test_forced_sampling_of_pr_and_main_and_no_stale_response_after_rate_limit(self):
        for selector in ("/pulls/1087","/git/ref/heads/main","/issues/1093"):
            with self.subTest(selector=selector):
                records=self.public_responses()
                with patch.object(self.identity,"_fetch",side_effect=records.__getitem__):
                    cb=self.authorize()
                    record=records[self.identity.API+selector]
                    if selector.startswith("/pulls"):
                        record["head"]["sha"]="e"*40
                    elif selector.startswith("/git"):
                        record["object"]["sha"]="e"*40
                    else:
                        record["updated_at"]=stamp(self.now)
                    with self.assertRaises(self.identity.IdentityHold):
                        cb(force=True)
        with patch.object(self.identity,"_current_authority",side_effect=ConnectionError("rate limited")):
            with self.assertRaises(self.identity.IdentityHold):
                self.authorize()

    def test_forged_signature_kty_and_malformed_base64_never_reach_public_refs(self):
        with patch.object(self.identity,"_current_authority",return_value=True) as live:
            token=self.token()
            head,payload,sig=token.split(".")
            new=sig[:-2]+("AA" if not sig.endswith("AA") else "QQ")
            self.request["authority"]["host_identity_token"]=".".join((head,payload,new))
            with self.assertRaises(self.identity.IdentityHold):
                self.identity.signed_authority(self.request)
            self.keys["keys"][0]["kty"]="oct"
            with self.assertRaises(self.identity.IdentityHold):
                self.authorize()
            self.assertEqual(live.call_count,0)

    def test_a_frozen_callback_rejects_mutation_of_request_after_authentication(self):
        with patch.object(self.identity,"_current_authority",return_value=True):
            cb=self.authorize()
            self.request["step"]="POST_D_E"
            with self.assertRaises(self.identity.IdentityHold):
                cb()

    def test_cache_never_outlives_an_entire_time_sample(self):
        with patch.object(self.identity,"_current_authority",return_value=True):
            with patch.object(self.identity.time,"monotonic",side_effect=(10,15)):
                with self.assertRaises(self.identity.IdentityHold):
                    self.authorize()

    def test_only_fixed_urls_use_tls_and_no_credentials_or_proxy(self):
        import io
        import urllib.request
        requests=[]
        class Response(io.BytesIO):
            status=200
            headers={"Content-Type":"application/json; charset=utf-8"}
            def geturl(self):
                return self_url
        class Opener:
            def open(self, req, timeout=None):
                requests.append((req,timeout))
                return Response(b'{"keys":[]}')
        self_url=self.identity.JWKS_URL
        with patch.object(self.identity.urllib.request,"build_opener",return_value=Opener()) as opened:
            self.assertEqual(self.identity._fetch(self_url),{"keys":[]})
            for url in ("https://attacker.example/jwks", "https://api.github.com@attacker.example",self_url+"?key=evil"):
                with self.assertRaises(self.identity.IdentityHold):
                    self.identity._fetch(url)
        self.assertEqual(len(requests),1)
        req,timeout=requests[0]
        self.assertEqual((req.get_method(),req.full_url),("GET", self_url))
        self.assertNotIn("Authorization",dict(req.header_items()))
        handlers=opened.call_args.args
        self.assertTrue(any(isinstance(h,urllib.request.ProxyHandler) and not h.proxies for h in handlers))
        self.assertTrue(any(isinstance(h,self.identity._Direct) and h.redirect_request(None) is None for h in handlers))
        self.assertLessEqual(timeout,4)

    def test_repeated_header_or_noncanonical_payload_cannot_select_another_key(self):
        def sign(header, payload):
            prefix=encode(header)+"."+encode(payload)
            return prefix+"."+encode(self.openssl("dgst","-sha256","-sign",str(self.key),data=prefix.encode()))
        for head,body in ((b'{"alg":"none","alg":"RS256","kid":"fixture-key","typ":"JWT"}',json_bytes(self.claims)),
                          (json_bytes(self.headers),json_bytes(self.claims)[:-1]+b',"actor":"mbalbo2023"}')):
            with self.subTest(head=head), self.assertRaises(self.identity.IdentityHold):
                self.request["authority"]["host_identity_token"]=sign(head,body)
                self.identity.signed_authority(self.request)

    def test_documented_github_x5t_is_a_thumbprint_and_cannot_select_another_key(self):
        cert=base64.b64decode(self.jwk["x5c"][0])
        thumb=encode(hashlib.sha1(cert,usedforsecurity=False).digest())
        with patch.object(self.identity,"_current_authority",return_value=True):
            self.assertTrue(self.authorize(header={**self.headers,"x5t":thumb})())
            with self.assertRaises(self.identity.IdentityHold):
                self.authorize(header={**self.headers,"x5t":encode(bytes(20))})
