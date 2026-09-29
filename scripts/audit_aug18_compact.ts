import { pool } from "../lib/db";
import { localDate } from "../lib/time";

const MONTHS: Record<string,string>={JAN:'01',FEB:'02',MAR:'03',APR:'04',MAY:'05',JUN:'06',JUL:'07',AUG:'08',SEP:'09',OCT:'10',NOV:'11',DEC:'12'};
function eventDate(t:string|null){if(!t)return null;const m=t.match(/-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(?:$|-)/i);return m?`20${m[1]}-${MONTHS[m[2].toUpperCase()]}-${m[3]}`:null}
function n(v:unknown){const x=Number(v);return v==null||v===''||!Number.isFinite(x)?null:x}

async function main(){
 if(!pool)throw new Error('DATABASE_URL required');
 const q=await pool.query<{
  id:string;session_id:string;mode_code:string|null;strategy_code:string;station_code:string;event_ticker:string|null;market_ticker:string;outcome_side:string;avg_fill_price:string|null;filled_qty:string;gross_cost:string;estimated_fee:string;decision_at:Date;auditor_status:string;evidence:Record<string,unknown>;observed_at:Date|null;timezone:string|null;
 }>(`SELECT o.id::text,p.session_id,p.mode_code,o.strategy_code,s.station_code,s.event_ticker,o.market_ticker,o.outcome_side,o.avg_fill_price::text,o.filled_qty::text,o.gross_cost::text,o.estimated_fee::text,o.decision_at,s.auditor_status,s.evidence,w.observed_at,st.timezone
       FROM paper_orders o JOIN paper_portfolios p ON p.id=o.portfolio_id JOIN paper_sessions ps ON ps.id=o.session_id JOIN paper_signals s ON s.id=o.signal_id LEFT JOIN live_weather_journal w ON w.id=s.weather_event_id LEFT JOIN stations st ON st.station_code=s.station_code
      WHERE ps.mode='paper_live' AND o.status IN ('filled','partial') AND o.decision_at>='2026-08-18T00:00:00Z'::timestamptz AND o.decision_at<'2026-08-19T12:00:00Z'::timestamptz ORDER BY o.id`);
 const rows=q.rows.map(r=>{
   const ed=eventDate(r.event_ticker); const wd=r.observed_at&&r.timezone?localDate(r.observed_at,r.timezone):null;
   const wrong=!!(ed&&wd&&ed!==wd); const nonApproved=r.auditor_status!=='approved';
   return {...r,eventDate:ed,weatherDate:wd,wrong,nonApproved};
 });
 const group=(key:'strategy_code'|'session_id'|'mode_code')=>Object.fromEntries([...new Set(rows.map(r=>String(r[key])))].sort().map(k=>{
   const a=rows.filter(r=>String(r[key])===k);return[k,{orders:a.length,wrongDate:a.filter(r=>r.wrong).length,nonApproved:a.filter(r=>r.nonApproved).length,clean:a.filter(r=>!r.wrong&&!r.nonApproved).length}]
 }));
 const target=rows.filter(r=>r.market_ticker==='KXHIGHNY-26AUG19-T85').map(r=>({orderId:r.id,sessionId:r.session_id,mode:r.mode_code,strategy:r.strategy_code,station:r.station_code,event:r.event_ticker,eventDate:r.eventDate,weatherDate:r.weatherDate,side:r.outcome_side,entry:n(r.avg_fill_price),qty:n(r.filled_qty),cost:n(r.gross_cost),fee:n(r.estimated_fee),auditorStatus:r.auditor_status,confirmedHighF:n(r.evidence?.confirmed_high_f),upperBoundF:n(r.evidence?.upper_bound_f),issues:[...(r.wrong?['WRONG_EVENT_DATE']:[]),...(r.nonApproved?['NON_APPROVED_SIGNAL_EXECUTED']:[])]}));
 const clean=rows.filter(r=>!r.wrong&&!r.nonApproved).map(r=>({orderId:r.id,sessionId:r.session_id,mode:r.mode_code,strategy:r.strategy_code,station:r.station_code,event:r.event_ticker,market:r.market_ticker,side:r.outcome_side,entry:n(r.avg_fill_price),qty:n(r.filled_qty)}));
 console.log(JSON.stringify({total:rows.length,wrongDate:rows.filter(r=>r.wrong).length,nonApproved:rows.filter(r=>r.nonApproved).length,ordersWithEitherIssue:rows.filter(r=>r.wrong||r.nonApproved).length,cleanOrders:clean.length,byStrategy:group('strategy_code'),bySession:group('session_id'),byMode:group('mode_code'),targetKnycAug19T85:target,cleanOrdersDetail:clean},null,2));
 await pool.end();
}
main().catch(async e=>{console.error('COMPACT_AUDIT_FAILED',e);await pool?.end().catch(()=>undefined);process.exit(1)});
