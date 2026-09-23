import { test } from 'node:test'
import assert from 'node:assert/strict'
import { utc, issueTime, pointsFor, summarize, validatePoints } from '../src/model.js'
test('backend timestamps use distinct zones and preserve explicit offsets',()=>{
  assert.equal(issueTime('2026-01-31T00:00:00').toISOString(),'2026-01-30T19:00:00.000Z')
  assert.equal(utc('2026-01-30T20:00:00').toISOString(),'2026-01-30T20:00:00.000Z')
  assert.equal(issueTime('2026-01-30T19:00:00Z').toISOString(),'2026-01-30T19:00:00.000Z')
})
const points=Array.from({length:48},(_,i)=>({turbine_id:1,valid_at:new Date(Date.UTC(2026,0,30,20+i)).toISOString(),normalized_power:i/48,wind_speed_ms:5}))
test('24/48 hour filtering retains separate turbines and chronological order',()=>{
 const run={points:[...points.slice().reverse(),...points.map(p=>({...p,turbine_id:2,normalized_power:.8}))]}
 assert.equal(pointsFor(run,1,24).length,24)
 assert.equal(pointsFor(run,2,48).length,48)
 assert.equal(pointsFor(run,1,24)[0].normalized_power,0)
 assert.equal(summarize(pointsFor(run,2,48)).mean.toFixed(2),'0.80')
})
test('missing hours, duplicates and invalid powers are flagged',()=>{
 assert.deepEqual(validatePoints(points,48,'2026-01-31T00:00:00'),[])
 assert.ok(validatePoints(points.slice(1),48,'2026-01-31T00:00:00').length>=2)
 assert.ok(validatePoints([{...points[0],normalized_power:2},points[0]],2,'2026-01-31T00:00:00').length>=2)
})
