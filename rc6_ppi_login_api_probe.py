#!/usr/bin/env python3
"""One-shot sanitized classifier for the exact PPI web login API.

The public PPI login bundle shows:
- base URL: https://api.portfoliopersonal.com/
- route: /api/Seguridad/Auth/Login
- JSON body keys: usuario / clave
- public client headers: AuthorizedClient=191206, ClientKey=pp123456

This probe performs exactly one authentication POST using credentials read only
from the restricted host secret. No credential, token, user object or message
value is printed. Only HTTP/envelope/boolean shape is returned.
"""
from __future__ import annotations
import argparse,json,os
from pathlib import Path

LOGIN_URL="https://cuenta.portfoliopersonal.com/login"
LOGIN_API="https://api.portfoliopersonal.com/api/Seguridad/Auth/Login"
PUBLIC_AUTHORIZED_CLIENT="191206"
PUBLIC_CLIENT_KEY="pp123456"

def parse_secret(path:Path)->tuple[str,str]:
    values={}
    for raw in path.read_text(encoding="utf-8").splitlines():
        s=raw.strip()
        if not s or s.startswith("#") or "=" not in s: continue
        k,v=s.split("=",1); values[k.strip()]=v.strip().strip('"').strip("'")
    return values.get("PPI_WEB_USERNAME",""),values.get("PPI_WEB_PASSWORD","")

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--profile",required=True); ap.add_argument("--secret",required=True); ap.add_argument("--chrome",default=os.getenv("POROTA_CHROME_EXECUTABLE","/usr/bin/google-chrome-stable")); args=ap.parse_args()
    secret=Path(args.secret); profile=Path(args.profile)
    if not secret.is_file() or not profile.is_dir():
        print(json.dumps({"status":"BLOCKED_LOCAL_PREREQUISITE","credentials_exposed":False,"real_orders_sent":0},sort_keys=True)); return 4
    user,password=parse_secret(secret)
    if not user or not password:
        print(json.dumps({"status":"BLOCKED_AUTH_LOCAL_SECRET_INCOMPLETE","credentials_exposed":False,"real_orders_sent":0},sort_keys=True)); return 4
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print(json.dumps({"status":"BLOCKED_PLAYWRIGHT_UNAVAILABLE","credentials_exposed":False,"real_orders_sent":0},sort_keys=True)); return 4

    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=str(profile),executable_path=args.chrome,headless=True,locale="es-AR",timezone_id="America/Argentina/Buenos_Aires",args=["--no-sandbox","--disable-dev-shm-usage"])
        page=ctx.pages[0] if ctx.pages else ctx.new_page(); page.goto(LOGIN_URL,wait_until="domcontentloaded",timeout=20000); page.wait_for_timeout(800)
        result=page.evaluate(
            """async ({u,p,url,ac,ck}) => {
              const safe={http_status:0,ok:false,json_object:false,envelope_success:false,envelope_status:null,payload_object:false,safe_payload_keys:[],has_token:false,twofa:false,change_password:false,network_error:false};
              try {
                const r=await fetch(url,{method:'POST',mode:'cors',credentials:'omit',headers:{'Accept':'application/json, text/plain, */*','Content-Type':'application/json','AuthorizedClient':ac,'ClientKey':ck},body:JSON.stringify({usuario:u,clave:p})});
                safe.http_status=r.status; safe.ok=r.ok;
                let data=null; try{data=await r.json()}catch(_){}
                if(data&&typeof data==='object'&&!Array.isArray(data)){
                  safe.json_object=true;
                  safe.envelope_success=!!data.success;
                  if(typeof data.status==='number'||typeof data.status==='string') safe.envelope_status=String(data.status).slice(0,24);
                  const payload=(data.payload&&typeof data.payload==='object'&&!Array.isArray(data.payload))?data.payload:data;
                  safe.payload_object=!!payload;
                  if(payload){
                    const allow=new Set(['twoFAInfo','changePassword','token','usuario','user','status']);
                    safe.safe_payload_keys=Object.keys(payload).filter(k=>allow.has(k)).sort().slice(0,20);
                    safe.has_token=!!(payload.token||payload.accessToken);
                    safe.twofa=!!payload.twoFAInfo;
                    safe.change_password=!!payload.changePassword;
                  }
                }
              }catch(_){safe.network_error=true}
              return safe;
            }""",
            {"u":user,"p":password,"url":LOGIN_API,"ac":PUBLIC_AUTHORIZED_CLIENT,"ck":PUBLIC_CLIENT_KEY},
        )
        ctx.close()

    code=int(result.get("http_status") or 0); status="AUTH_API_UNCLASSIFIED"
    if result.get("network_error"): status="BLOCKED_AUTH_API_NETWORK_ERROR"
    elif result.get("twofa"): status="AUTH_API_2FA_REQUIRED"
    elif result.get("change_password"): status="AUTH_API_PASSWORD_CHANGE_REQUIRED"
    elif 200<=code<300 and result.get("has_token"): status="AUTH_API_CREDENTIALS_ACCEPTED"
    elif 200<=code<300 and result.get("json_object"): status="AUTH_API_2XX_WITHOUT_AUTH_SHAPE"
    elif code in (400,401): status="AUTH_API_CREDENTIALS_REJECTED_OR_CHALLENGE"
    elif code==403: status="AUTH_API_FORBIDDEN_OR_CHALLENGE"
    elif code==429: status="AUTH_API_RATE_LIMITED"
    elif code>=500: status="AUTH_API_SERVER_ERROR"
    elif code: status="AUTH_API_RESPONSE_UNCLASSIFIED"
    out={"status":status,"http_status":code,"ok":bool(result.get("ok")),"json_object":bool(result.get("json_object")),"envelope_success":bool(result.get("envelope_success")),"envelope_status":result.get("envelope_status"),"payload_object":bool(result.get("payload_object")),"safe_payload_keys":result.get("safe_payload_keys",[])[:20],"has_token_shape":bool(result.get("has_token")),"twofa_shape":bool(result.get("twofa")),"change_password_shape":bool(result.get("change_password")),"credentials_exposed":False,"request_count":1,"orders_visited":False,"real_orders_sent":0}
    print(json.dumps(out,sort_keys=True)); return 0

if __name__=="__main__": raise SystemExit(main())
