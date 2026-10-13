import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chooseHistoryObjects, chooseSolarObjects, parseSolarObjects, readSolarFile, replayDue } from "../lib/weather/solar-source";
import { scanAngles, SOLAR_STATIONS } from "../lib/weather/solar-model";

test("source uses completed scan midpoints and actual publication timestamps", () => {
  const objects = parseSolarObjects('<ListBucketResult><Contents><Key>ABI-L2-DSRF/2026/285/21/OR_ABI-L2-DSRF-M6_G18_s20262852100202_e20262852109511_c20262852115445.nc</Key><LastModified>2026-10-12T21:15:48.000Z</LastModified></Contents></ListBucketResult>');
  assert.equal(objects[0]?.time, "2026-10-12T21:05:05.650Z");
  assert.equal(objects[0]?.publishedAt, "2026-10-12T21:15:48.000Z");
});
test("startup trend selects approximately 30 minutes of history across hour boundaries", () => {
  const objects = [0,10,20,30,40,50].map(n => ({ key: String(n), time: new Date(Date.UTC(2026,9,12,21,n)).toISOString(), scanStart: "", scanEnd: "", publishedAt: "" }));
  assert.deepEqual(chooseSolarObjects(objects).map(x => x.key), ["50","20"]);
});
test("today's replay keeps every published daylight scan in local-date order", () => {
  const lax=SOLAR_STATIONS.find(s => s.stid === "KLAX")!;
  const objects=["2026-10-11T21:05:00Z","2026-10-12T10:05:00Z","2026-10-12T21:05:00Z","2026-10-12T21:15:00Z","2026-10-12T21:35:00Z","2026-10-13T03:05:00Z"].map(time=>({time,key:time,scanStart:time,scanEnd:time,publishedAt:time}));
  assert.deepEqual(chooseHistoryObjects(objects,[lax],new Date("2026-10-13T02:00:00Z")).map(x=>x.time),["2026-10-12T21:05:00Z","2026-10-12T21:15:00Z","2026-10-12T21:35:00Z"]);
});
test("replay revisits today's archive to recover scans published during an idle gap", () => {
  assert.equal(replayDue(false,true,600000),true);
  assert.equal(replayDue(false,false,0),true);
  assert.equal(replayDue(true,true,600000),false);
  assert.equal(replayDue(false,true,10000),false);
});
test("compressed HDF5 decoder reads the station pixel and masks invalid neighborhood values", async () => {
  const h5 = await import("h5wasm/node");
  await h5.ready;
  const dir = await mkdtemp(join(tmpdir(), "solar-test-"));
  const path = join(dir,"fixture.nc");
  const lax = SOLAR_STATIONS.find(s => s.stid === "KLAX")!;
  const angle = scanAngles(lax.lat,lax.lon,-137);
  const f = new h5.File(path,"w");
  try {
    const projection = f.create_dataset({name:"goes_imager_projection",data:0});
    projection.create_attribute("longitude_of_projection_origin",-137);
    for (const [axis, values] of [["x",[angle.x - 0.000056,angle.x,angle.x + 0.000056]],["y",[angle.y + 0.000056,angle.y,angle.y - 0.000056]]] as const) {
      const d = f.create_dataset({name:axis,data:new Float64Array(values)});
      d.create_attribute("scale_factor",1); d.create_attribute("add_offset",0);
    }
    const dsr=f.create_dataset({name:"DSR",data:new Uint16Array([100,100,65535,100,1234,100,100,100,100]),shape:[3,3],chunks:[3,3],compression:6});
    dsr.create_attribute("scale_factor",0.5); dsr.create_attribute("add_offset",-10); dsr.create_attribute("_FillValue",65535);
    f.create_dataset({name:"DQF",data:new Uint8Array([0,0,0,1,0,0,0,0,0]),shape:[3,3]});
  } finally { f.close(); }
  try {
    const result=await readSolarFile(path,[lax]);
    assert.equal(result.KLAX?.watts,607);
    assert.equal(result.KLAX?.nearbyWatts,40);
    assert.equal(result.KLAX?.validPixels,7);
  } finally { await rm(dir,{recursive:true,force:true}); }
});
