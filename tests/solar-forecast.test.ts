import assert from 'node:assert/strict';
import test from 'node:test';
import { forecastAt, forecastGap, eligibleForecast, dswrfRange, type SolarForecastPoint } from '../lib/weather/solar-forecast-model';
import { SOLAR_STATIONS } from '../lib/weather/solar-model';
const station = SOLAR_STATIONS[0];
const point = (time: string, watts: number): SolarForecastPoint => ({time,watts,runAt:'2026-10-14T00:00:00.000Z',publishedAt:'2026-10-14T01:10:22.000Z',capturedAt:'2026-10-14T15:00:00.000Z',sourceUrl:'https://noaa-hrrr-bdp-pds.s3.amazonaws.com/test',gridLat:40.77,gridLon:-73.97});
const points = [point('2026-10-14T14:00:00Z',300),point('2026-10-14T15:00:00Z',500)];
test('matches instantaneous irradiance at scan time by interpolation, never extrapolation',()=>{
 assert.equal(forecastAt(points,'2026-10-14T14:30:00Z'),400);
 assert.equal(forecastAt(points,'2026-10-14T15:00:00Z'),500);
 assert.equal(forecastAt(points,'2026-10-14T15:05:00Z'),null);
 assert.equal(forecastAt([points[0],point('2026-10-14T16:00:00Z',700)],'2026-10-14T15:00:00Z'),null);
});
test('different runs and invalid radiation never form a forecast path',()=>{
 assert.equal(forecastAt([points[0],{...points[1],runAt:'2026-10-14T06:00:00Z'}],'2026-10-14T14:30:00Z'),null);
 assert.equal(forecastAt([{...points[0],watts:NaN}],'2026-10-14T14:00:00Z'),null);
});
test('accepts only the designated previous-evening run published before the local day',()=>{
 assert.equal(eligibleForecast(points[0],station,'2026-10-14'),true);
 assert.equal(eligibleForecast({...points[0],publishedAt:'2026-10-14T04:01:00Z'},station,'2026-10-14'),false);
 assert.equal(eligibleForecast({...points[0],runAt:'2026-10-14T12:00:00Z'},station,'2026-10-14'),false);
 assert.equal(eligibleForecast({...points[0],publishedAt:'2026-10-13T23:00:00Z'},station,'2026-10-14'),false);
 assert.equal(eligibleForecast({...points[0],publishedAt:'2026-10-14T16:00:00Z'},SOLAR_STATIONS[2],'2026-10-14'),false);
});
test('deviation uses absolute sunlight difference and an explicitly descriptive display band',()=>{
 assert.deepEqual(forecastGap(600,400,700),{expectedWatts:400,deltaWatts:200,percent:50,tone:'more',bandWatts:70});
 assert.equal(forecastGap(380,400,700)?.tone,'near');
 assert.equal(forecastGap(300,400,700)?.tone,'less');
 assert.equal(forecastGap(40,0,50)?.percent,null);
 assert.equal(forecastGap(null,400,700),null);
 assert.equal(forecastGap(400,null,700),null);
});
test('range inventory selects instantaneous surface DSWRF rather than an average',()=>{
 const idx='1:0:d=2026101400:TMP:surface:15 hour fcst:\n2:40:d=2026101400:DSWRF:surface:14-15 hour ave fcst:\n3:90:d=2026101400:DSWRF:surface:15 hour fcst:\n4:140:d=2026101400:DLWRF:surface:15 hour fcst:';
 assert.deepEqual(dswrfRange(idx,15),{start:90,end:139});
 assert.equal(dswrfRange(idx,16),null);
});

test('rejects decoder metadata mismatches and preserves actual nearest grid coordinates',async()=>{
 const { parseForecastDecode } = await import('../lib/weather/solar-forecast-source');
 const keys = {typeOfLevel:'surface',level:0,discipline:0,parameterCategory:4,parameterNumber:7,shortName:'sdswrf',units:'W m**-2',stepType:'instant',dataDate:20261014,dataTime:0,validityDate:20261014,validityTime:1500,step:15};
 const neighbours=[{value:508.7,latitude:40.7884,longitude:286.035,distance:1.11}];
 const output=JSON.stringify([{keys,neighbours}]);
 const decoded=parseForecastDecode(output,'2026-10-14',15);
 assert.equal(decoded.watts,508.7);
 assert.equal(decoded.gridLat,40.7884);
 assert.ok(Math.abs(decoded.gridLon!+73.965)<0.001);
 for (const change of [{typeOfLevel:'heightAboveGround'},{parameterNumber:8},{stepType:'avg'},{units:'J m**-2'},{dataTime:600},{validityTime:1400}]) {
  assert.throws(()=>parseForecastDecode(JSON.stringify([{keys:{...keys,...change},neighbours}]),'2026-10-14',15));
 }
 assert.throws(()=>parseForecastDecode(JSON.stringify([{keys,neighbours:[{...neighbours[0],value:9999}]}]),'2026-10-14',15));
});

test('an unavailable persistence archive does not block loading the original NOAA forecast',async()=>{
 const { readForecastArchive } = await import('../lib/weather/solar-forecast-source');
 assert.deepEqual(await readForecastArchive('2026-10-14',async()=>{throw new Error('database offline');}),[]);
});

test('accepts the Debian ecCodes dswrf alias for the same instantaneous surface field',async()=>{
 const {parseForecastDecode}=await import('../lib/weather/solar-forecast-source');
 const keys={typeOfLevel:'surface',level:0,discipline:0,parameterCategory:4,parameterNumber:7,shortName:'dswrf',units:'W m**-2',stepType:'instant',dataDate:20261014,dataTime:0,validityDate:20261014,validityTime:1500,step:15};
 assert.equal(parseForecastDecode(JSON.stringify([{keys,neighbours:[{value:508.7,latitude:40.7884,longitude:286.035,distance:1.11}]}]),'2026-10-14',15).watts,508.7);
});

test('a failed archive write remains retryable with the identical frozen points',async()=>{
 const {saveForecastArchive}=await import('../lib/weather/solar-forecast-source');
 const rows=[{stid:'KNYC',point:points[0]}];
 assert.equal(await saveForecastArchive('2026-10-14',rows,async()=>{throw new Error('write offline');}),null);
 const restored=await saveForecastArchive('2026-10-14',rows,async(_date,received)=>received);
 assert.deepEqual(restored,rows);
});

test('cancels an unexpected whole-file response instead of downloading a full model grid',async(t)=>{
 const {fetchForecastHour}=await import('../lib/weather/solar-forecast-source');
 let cancelled=false;
 t.mock.method(globalThis,'fetch',async(input:RequestInfo | URL)=>String(input).endsWith('.idx') ? new Response('1:90:d=2026101400:DSWRF:surface:15 hour fcst:\n2:140:d=2026101400:DLWRF:surface:15 hour fcst:') : new Response(new ReadableStream({cancel(){cancelled=true;}}),{status:200}));
 await assert.rejects(fetchForecastHour('2026-10-14',15),/range response mismatch/);
 assert.equal(cancelled,true);
});

test('complete memory coverage cannot suppress a pending archive retry',async()=>{
 const {forecastRunDue}=await import('../lib/weather/solar-forecast-source');
 assert.equal(forecastRunDue(false,true,true,360000),true);
 assert.equal(forecastRunDue(false,true,false,360000),false);
 assert.equal(forecastRunDue(true,true,true,360000),false);
 assert.equal(forecastRunDue(false,true,true,60000),false);
 assert.equal(forecastRunDue(false,false,false,360000),true);
});
