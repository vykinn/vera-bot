import json,os,time,hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
C={};S=set();V={};T=time.time()
def get(s,i): x=C.get((s,i)); return x[1] if x else None
def ctx(b):
 s,i,v,p=b.get("scope"),b.get("context_id"),b.get("version"),b.get("payload")
 if s not in ("category","merchant","customer","trigger"): return 400,{"accepted":False,"reason":"invalid_scope"}
 if not isinstance(i,str) or not isinstance(v,int) or isinstance(v,bool) or not isinstance(p,dict): return 400,{"accepted":False,"reason":"invalid_context"}
 k=(s,i)
 if k in C and C[k][0]>=v:return 409,{"accepted":False,"reason":"stale_version","current_version":C[k][0]}
 C[k]=(v,p);return 200,{"accepted":True,"ack_id":f"ack_{i}_v{v}"}
def name(m):
 return ((m.get("identity")or{}).get("owner_first_name")or(m.get("identity")or{}).get("name")or"there").replace("Dr. ","").strip()
def cat(m):return get("category",m.get("category_slug"))or{}
def msg(t,m,c):
 p=t.get("payload")or{}; n=name(m); b=(m.get("identity")or{}).get("name","your business");k=t.get("kind","")
 if p.get("placeholder"):p={}
 if k=="perf_dip":
  x=p.get("metric","views");d=p.get("delta_pct")
  if d is not None and d<0:return f"{n}, {x.replace('_',' ')} are down {abs(d)*100:.0f}% this week. Want me to draft the quickest fix? Reply YES."
 if k=="perf_spike" and p.get("delta_pct") is not None:return f"{n}, good news — {p.get('metric','views')} are up {abs(p['delta_pct'])*100:.0f}% this week. Want me to draft a follow-up post? Reply YES."
 if k=="renewal_due":return f"{n}, your plan renews in {p.get('days_remaining',(m.get('subscription')or{}).get('days_remaining'))} days. Want the renewal link? Reply YES."
 if k=="competitor_opened":return f"{n}, {p.get('competitor_name','a new competitor')} just opened nearby. Your own customer feedback is the useful differentiator. Want a post draft? Reply YES."
 if k=="review_theme_emerged":return f"{n}, {p.get('occurrences_30d','Several')} reviews mention {p.get('theme','a recurring theme')}. Want replies plus one practical fix? Reply YES."
 if k=="festival_upcoming":return f"{n}, {p.get('festival','the festival')} is {p.get('days_until','soon')} days out. Want a focused seasonal package + post draft? Reply YES."
 if k=="supply_alert":return f"{n}, urgent supply alert for {p.get('molecule','the affected product')}. Want customer communication + replacement steps? Reply YES."
 if k in ("regulation_change","compliance_alert"):return f"{n}, compliance heads-up for your category. Want a concise audit checklist? Reply YES."
 if k=="research_digest":return f"{n}, I found a new research update relevant to your category. Want a 2-minute summary + customer WhatsApp draft? Reply YES."
 if k=="gbp_unverified":return f"{n}, {b}'s Google profile is still unverified. Want me to walk you through verification? Reply YES."
 if k in ("customer_lapsed_soft","customer_lapsed_hard"):return f"Hi, {b} here. It's been a while since your last visit — no judgment. Reply YES and we'll hold a slot this week."
 if k=="appointment_tomorrow":return f"Hi, a quick reminder from {b}: your appointment is tomorrow. Reply YES to confirm, or tell us if you need to reschedule."
 if k=="recall_due":return f"Hi, {b} here. Your next visit is due. Reply YES and we'll hold a suitable slot."
 if k=="chronic_refill_due":return f"Hi, {b} here. Your regular refill is due. Reply CONFIRM to dispatch, or tell us if anything changed."
 if k=="trial_followup":return f"Hi, {b} here — thanks for coming in for the trial. Reply YES to keep the momentum going."
 if k=="wedding_package_followup":return f"Hi, {b} here. Reply YES and we'll hold a slot for your next step."
 return f"{n}, quick update for {b}. I found a useful action from your current context. Want me to set it up? Reply YES."
def allowed(t,c):
 if not c:return True
 co=c.get("consent")or{};pr=c.get("preferences")or{}
 if co.get("opted_out")or pr.get("opted_out"):return False
 if pr.get("reminder_opt_in"):return True
 return bool(co.get("scope"))
def tick(b):
 out=[];used=set()
 for tid in b.get("available_triggers")or[]:
  t=get("trigger",tid)
  if not t or (t.get("suppression_key")or tid) in S:continue
  mid=t.get("merchant_id")or(t.get("payload")or{}).get("merchant_id");m=get("merchant",mid)
  c=get("customer",t.get("customer_id")) if t.get("customer_id") else None
  if not m or (t.get("scope")=="customer" and not c) or not allowed(t,c):continue
  rec=("c",c.get("customer_id")) if c else ("m",mid)
  if rec in used:continue
  sk=t.get("suppression_key")or tid;cid="conv_"+hashlib.sha1(sk.encode()).hexdigest()[:12];S.add(sk);used.add(rec)
  V[cid]={"merchant_id":mid,"customer_id":c.get("customer_id")if c else None}
  out.append({"conversation_id":cid,"merchant_id":mid,"customer_id":c.get("customer_id")if c else None,"send_as":"merchant_on_behalf"if c else"vera","trigger_id":tid,"template_name":"vera_"+t.get("kind","generic")+"_v1","template_params":[],"body":msg(t,m,cat(m)),"cta":"binary_yes_no","suppression_key":sk,"rationale":"Deterministic, context-grounded message with one CTA."})
  if len(out)>=20:break
 return {"actions":out}
class H(BaseHTTPRequestHandler):
 protocol_version="HTTP/1.1"
 def log_message(self,*a):pass
 def out(self,n,x):
  d=json.dumps(x,ensure_ascii=False).encode();self.send_response(n);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(d)));self.end_headers();self.wfile.write(d)
 def body(self):
  try:return json.loads(self.rfile.read(int(self.headers.get("Content-Length","0")))or b"{}")
  except:return None
 def do_GET(self):
  p=self.path.split("?")[0].rstrip("/")
  if p=="/v1/healthz":return self.out(200,{"status":"ok","uptime_seconds":int(time.time()-T),"contexts_loaded":{s:sum(1 for x in C if x[0]==s)for s in("category","merchant","customer","trigger")}})
  if p=="/v1/metadata":return self.out(200,{"team_name":"Vivek Kumar","team_members":["Vivek Kumar"],"model":"deterministic rule-based composer","version":"1.0.0"})
  return self.out(200,{"service":"vera-bot","endpoints":["/v1/healthz","/v1/metadata","/v1/context","/v1/tick","/v1/reply"]})
 def do_POST(self):
  p=self.path.split("?")[0].rstrip("/");b=self.body()
  if b is None:return self.out(400,{"error":"malformed_json"})
  if p=="/v1/context":n,x=ctx(b);return self.out(n,x)
  if p=="/v1/tick":return self.out(200,tick(b))
  if p=="/v1/reply":
   v=V.get(b.get("conversation_id"))
   if not v:return self.out(404,{"action":"wait","rationale":"conversation not found"})
   m=str(b.get("message","")).lower().strip()
   if m in("stop","unsubscribe","remove me"):return self.out(200,{"action":"end","rationale":"opt-out honored"})
   if m in("yes","y","confirm","1"):return self.out(200,{"action":"send","message":"Done — I'll take care of that next.","rationale":"acceptance routed to the prepared action"})
   if m in("no","n","later","not now"):return self.out(200,{"action":"wait","wait_seconds":86400,"rationale":"decline/later honored"})
   return self.out(200,{"action":"send","message":"Got it. Tell me what you'd like to change.","rationale":"clarification response"})
  if p=="/v1/teardown":C.clear();S.clear();V.clear();return self.out(200,{"ok":True})
  return self.out(404,{"error":"not_found"})
if __name__=="__main__":ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","8080"))),H).serve_forever()
