import { pool } from "../lib/db";

const targets=[
 {stid:"KNYC",tz:"America/New_York",series:"KXHIGHNY"},
 {stid:"KPHL",tz:"America/New_York",series:"KXHIGHPHIL"},
 {stid:"KLAX",tz:"America/Los_Angeles",series:"KXHIGHLAX"},
 {stid:"KDEN",tz:"America/Denver",series:"KXHIGHDEN"},
 {stid:"KSEA",tz:"America/Los_Angeles",series:"KXHIGHTSEA"},
];
const dates=["2026-09-27","2026-09-28"];
const mon=["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
const eventTicker=(series:string,date:string)=>{const d=new Date(date+"T00:00:00Z");return `${series}-${String(d.getUTCFullYear()).slice(2)}${mon[d.getUTCMonth()]}${String(d.getUTCDate()).padStart(2,"0")}`};
const num=(v:any)=>{const x=Number(v);return v==null||!Number.isFinite(x)?null:x};
const localDate=(epochSec:number,tz:string)=>new Intl.DateTimeFormat("en-CA",{timeZone:tz,year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date(epochSec*1000));

async function main(){
 if(!pool)throw new Error("DATABASE_URL required");
 const matrix:any[]=[];
 for(const t of targets)for(const date of dates){
  const q=await pool.query(`
   WITH cuts(label,cut_at) AS (VALUES
    ('preday',$2::date::timestamp AT TIME ZONE $3),
    ('12local',($2::date+time '12:00') AT TIME ZONE $3),
    ('14local',($2::date+time '14:00') AT TIME ZONE $3),
    ('18local',($2::date+time '18:00') AT TIME ZONE $3),
    ('endday',(($2::date+1)::timestamp) AT TIME ZONE $3))
   SELECT c.label,
    twc.captured_at twc_at,NULLIF(twc.daily_highs->>($2::text),'')::float8 twc_high,
    nws.captured_at nws_at,NULLIF(nws.daily_highs->>($2::text),'')::float8 nws_high
   FROM cuts c
   LEFT JOIN LATERAL(SELECT captured_at,daily_highs FROM weather_forecast_snapshots WHERE stid=$1 AND source='twc' AND captured_at<c.cut_at AND daily_highs?($2::text) ORDER BY captured_at DESC LIMIT 1)twc ON true
   LEFT JOIN LATERAL(SELECT captured_at,daily_highs FROM weather_forecast_snapshots WHERE stid=$1 AND source='nws' AND captured_at<c.cut_at AND daily_highs?($2::text) ORDER BY captured_at DESC LIMIT 1)nws ON true
   ORDER BY CASE c.label WHEN 'preday' THEN 0 WHEN '12local' THEN 1 WHEN '14local' THEN 2 WHEN '18local' THEN 3 ELSE 4 END
  `,[t.stid,date,t.tz]);
  matrix.push({stid:t.stid,date,cuts:q.rows});
 }

 const kalshi:any[]=[];
 for(const t of targets)for(const date of dates){
  const ev=eventTicker(t.series,date);
  let payload:any=null,error:any=null,used:any=null;
  for(const base of ["https://api.elections.kalshi.com/trade-api/v2","https://external-api.kalshi.com/trade-api/v2"]){
   try{
    const u=new URL(base+"/markets");u.searchParams.set("event_ticker",ev);u.searchParams.set("limit","1000");
    const r=await fetch(u,{headers:{"User-Agent":"MercuryEdge forecast research"}});
    if(r.ok){payload=await r.json();used=u.toString();break}else error=`${r.status}:${(await r.text()).slice(0,200)}`;
   }catch(e){error=String(e)}
  }
  const markets=Array.isArray(payload?.markets)?payload.markets:[];
  const compact=markets.map((m:any)=>({ticker:m.ticker,status:m.status,result:m.result,title:m.title,subtitle:m.subtitle,yes_sub_title:m.yes_sub_title,floor_strike:num(m.floor_strike),cap_strike:num(m.cap_strike),last_price:num(m.last_price)}));
  kalshi.push({stid:t.stid,date,eventTicker:ev,used,error,count:compact.length,winners:compact.filter((m:any)=>String(m.result).toLowerCase()==="yes"),markets:compact});
 }

 const u=new URL("https://aviationweather.gov/api/data/metar");
 u.searchParams.set("ids",targets.map(x=>x.stid).join(","));u.searchParams.set("format","json");u.searchParams.set("hours","60");
 let raw:any[]=[];try{const r=await fetch(u,{headers:{"User-Agent":"MercuryEdge forecast research"}});if(r.ok)raw=await r.json();else console.error("AWC_HTTP",r.status)}catch(e){console.error("AWC_FETCH",e)}
 const wxSummary:any[]=[];
 for(const t of targets)for(const date of dates){
  const rows=raw.filter((x:any)=>x.icaoId===t.stid&&num(x.obsTime)!=null&&localDate(Number(x.obsTime),t.tz)===date);
  const temps=rows.map((x:any)=>num(x.temp)).filter((x:any)=>x!=null) as number[];
  const dewps=rows.map((x:any)=>num(x.dewp)).filter((x:any)=>x!=null) as number[];
  const maxC=temps.length?Math.max(...temps):null;
  const maxRows=maxC==null?[]:rows.filter((x:any)=>num(x.temp)===maxC);
  const rain=rows.filter((x:any)=>/RA|DZ|TS/.test(String(x.wxString||"")+" "+String(x.rawOb||""))).length;
  const lowCloud=rows.filter((x:any)=>Array.isArray(x.clouds)&&x.clouds.some((c:any)=>["BKN","OVC","OVX"].includes(c.cover)&&num(c.base)!=null&&Number(c.base)<=3000)).length;
  wxSummary.push({stid:t.stid,date,n:rows.length,maxF:maxC==null?null:maxC*9/5+32,maxTimes:maxRows.map((x:any)=>new Date(Number(x.obsTime)*1000).toISOString()),meanDewF:dewps.length?(dewps.reduce((a,b)=>a+b,0)/dewps.length)*9/5+32:null,rainFrac:rows.length?rain/rows.length:null,lowCloudFrac:rows.length?lowCloud/rows.length:null});
 }
 console.log("FORECAST_MATRIX="+JSON.stringify(matrix));
 console.log("KALSHI_RESULTS="+JSON.stringify(kalshi));
 console.log("WX_SUMMARY="+JSON.stringify(wxSummary));
 await pool.end();
}
main().catch(async e=>{console.error("FORECAST_STUDY_FAILED",e);await pool?.end().catch(()=>undefined);process.exit(1)});
