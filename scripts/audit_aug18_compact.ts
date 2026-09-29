import { pool } from "../lib/db";

const targets = [
  {stid:"KNYC", tz:"America/New_York", series:"KXHIGHNY"},
  {stid:"KPHL", tz:"America/New_York", series:"KXHIGHPHIL"},
  {stid:"KLAX", tz:"America/Los_Angeles", series:"KXHIGHLAX"},
  {stid:"KDEN", tz:"America/Denver", series:"KXHIGHDEN"},
  {stid:"KSEA", tz:"America/Los_Angeles", series:"KXHIGHTSEA"},
];
const dates=["2026-09-27","2026-09-28"];

function evt(series:string,date:string){
  const d=new Date(date+"T00:00:00Z");
  const mon=["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"][d.getUTCMonth()];
  return `${series}-${String(d.getUTCFullYear()).slice(2)}${mon}${String(d.getUTCDate()).padStart(2,"0")}`;
}
function n(v:any){const x=Number(v);return v==null||!Number.isFinite(x)?null:x}

async function main(){
 if(!pool) throw new Error("DATABASE_URL required");
 const matrix:any[]=[];
 for(const t of targets){
  for(const date of dates){
   const q=await pool.query(`
     WITH cuts(label,cut_at) AS (
       VALUES
       ('preday', $2::date::timestamp AT TIME ZONE $3),
       ('09local', ($2::date + time '09:00') AT TIME ZONE $3),
       ('12local', ($2::date + time '12:00') AT TIME ZONE $3),
       ('14local', ($2::date + time '14:00') AT TIME ZONE $3),
       ('18local', ($2::date + time '18:00') AT TIME ZONE $3),
       ('endday', (($2::date + 1)::timestamp) AT TIME ZONE $3)
     )
     SELECT c.label,
       twc.captured_at twc_at, NULLIF(twc.daily_highs->>($2::text),'')::float8 twc_high,
       nws.captured_at nws_at, NULLIF(nws.daily_highs->>($2::text),'')::float8 nws_high
     FROM cuts c
     LEFT JOIN LATERAL (
       SELECT captured_at,daily_highs FROM weather_forecast_snapshots
       WHERE stid=$1 AND source='twc' AND captured_at < c.cut_at AND daily_highs ? ($2::text)
       ORDER BY captured_at DESC LIMIT 1
     ) twc ON true
     LEFT JOIN LATERAL (
       SELECT captured_at,daily_highs FROM weather_forecast_snapshots
       WHERE stid=$1 AND source='nws' AND captured_at < c.cut_at AND daily_highs ? ($2::text)
       ORDER BY captured_at DESC LIMIT 1
     ) nws ON true
     ORDER BY CASE c.label WHEN 'preday' THEN 0 WHEN '09local' THEN 1 WHEN '12local' THEN 2 WHEN '14local' THEN 3 WHEN '18local' THEN 4 ELSE 5 END
   `,[t.stid,date,t.tz]);
   matrix.push({stid:t.stid,date,cuts:q.rows});
  }
 }

 const kalshi:any[]=[];
 for(const t of targets){
   for(const date of dates){
     const eventTicker=evt(t.series,date);
     const urls=[
       `https://api.elections.kalshi.com/trade-api/v2/events/${eventTicker}?with_nested_markets=true`,
       `https://external-api.kalshi.com/trade-api/v2/events/${eventTicker}?with_nested_markets=true`
     ];
     let payload:any=null, used:string|null=null, error:string|null=null;
     for(const url of urls){
       try{const r=await fetch(url,{headers:{"User-Agent":"MercuryEdge forecast research"}});if(r.ok){payload=await r.json();used=url;break}else error=`${r.status} ${await r.text()}`;}catch(e){error=String(e)}
     }
     const markets=payload?.markets ?? payload?.event?.markets ?? [];
     const winners=(Array.isArray(markets)?markets:[]).filter((m:any)=>String(m.result||"").toLowerCase()==="yes").map((m:any)=>({
       ticker:m.ticker,title:m.title,subtitle:m.subtitle,yes_sub_title:m.yes_sub_title,
       floor_strike:n(m.floor_strike),cap_strike:n(m.cap_strike),result:m.result,status:m.status
     }));
     kalshi.push({stid:t.stid,date,eventTicker,used,error,winners,marketCount:Array.isArray(markets)?markets.length:null,eventStatus:payload?.event?.status??null});
   }
 }

 const awcUrl=new URL("https://aviationweather.gov/api/data/metar");
 awcUrl.searchParams.set("ids",targets.map(t=>t.stid).join(","));
 awcUrl.searchParams.set("format","json");
 awcUrl.searchParams.set("hours","60");
 let awc:any[]=[];
 try{const r=await fetch(awcUrl,{headers:{"User-Agent":"MercuryEdge forecast research"}}); if(r.ok) awc=await r.json(); else console.error("AWC_HTTP",r.status,await r.text());}catch(e){console.error("AWC_FETCH",e)}
 const wx=awc.filter((x:any)=> {
   const tm=n(x.obsTime); if(tm==null)return false;
   const iso=new Date(tm*1000).toISOString();
   return iso>="2026-09-27T00:00:00.000Z"&&iso<"2026-09-29T12:00:00.000Z";
 }).map((x:any)=>({stid:x.icaoId,obsTime:x.obsTime,tempC:n(x.temp),dewC:n(x.dewp),wdir:n(x.wdir),wspdKt:n(x.wspd),raw:x.rawOb,clouds:x.clouds??null,wx:x.wxString??null}));

 console.log("FORECAST_MATRIX="+JSON.stringify(matrix));
 console.log("KALSHI_RESULTS="+JSON.stringify(kalshi));
 console.log("AWC_ROWS="+JSON.stringify(wx));
 await pool.end();
}
main().catch(async e=>{console.error("FORECAST_STUDY_FAILED",e);await pool?.end().catch(()=>undefined);process.exit(1)});
