# C# templates for `send_code_to_revit`

Each block below is a complete body to send via `mcp-server-for-revit → send_code_to_revit`.
They are **not** standalone programs — the host inserts them into a method with `document`,
`Autodesk.Revit.DB`, `System`, `System.Collections.Generic`, `System.Linq` already in scope,
and wraps them in its own transaction. Edit the marked constants per project and per chunk.

**Before the first run**, skim [troubleshooting.md](troubleshooting.md) — the template has
sharp edges (no top-level `using`, `document` not `doc`, no nested `Transaction`,
`ElementId.Value`, ElementId-typed Scope Box) that cause compile errors or silent no-ops.

Everything below shares this **preamble** (paste at the top of each block, edit the two
constants and the map):

```csharp
Document doc = document;
if(doc.Title!="ACCC_D_EL_SL") return "WRONG DOC: "+doc.Title;   // <-- DOC_TITLE guard
var sw=System.Diagnostics.Stopwatch.StartNew();
var log=new System.Text.StringBuilder(); void Log(string m)=>log.AppendLine(m);
const int VIEW_SKIP=0, VIEW_TAKE=50;                            // <-- chunk window
const double ANNO_TOL=5e-4, GRID_TOL=5e-3, TITLE_TOL=1e-3, BOX_TOL=1e-3;
var REF=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase){        // sector -> template sheet
 {"SECTOR A","E300A.A"},{"SECTOR B","E300A.B"},{"SECTOR C","E300A.C"},
 {"SECTOR D","E300A.D"},{"SECTOR E","E300A.E"},{"SECTOR F","E300A.F"}};
var TARGET_TYPES=new HashSet<ViewType>{ViewType.FloorPlan,ViewType.CeilingPlan};
var scopeBoxNames=new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_VolumeOfInterest)
  .WhereElementIsNotElementType().ToDictionary(e=>e.Id,e=>e.Name);
string GetScopeBox(View v){ var p=v.LookupParameter("Scope Box"); if(p==null)return "";
  if(p.StorageType==StorageType.ElementId){ var e=p.AsElementId();
    if(e==null||e==ElementId.InvalidElementId)return "";
    return scopeBoxNames.TryGetValue(e,out var n)?n:""; }
  return p.AsValueString()??""; }   // Scope Box is ElementId-typed; AsString() returns ""
var vpByView=new Dictionary<long,Viewport>();
foreach(var vp in new FilteredElementCollector(doc).OfClass(typeof(Viewport)).Cast<Viewport>())
  if(!vpByView.ContainsKey(vp.ViewId.Value)) vpByView[vp.ViewId.Value]=vp;
var allSheets=new FilteredElementCollector(doc).OfClass(typeof(ViewSheet)).Cast<ViewSheet>().ToList();
```

And this **grid-signature helper** (paste after the preamble in any block that touches grids):

```csharp
// returns per-view: 4 anno offsets, and per-grid {gapHi, gapLo} = endpoint gap to crop edge along grid axis
System.Func<View,(double[] anno,Dictionary<long,double[]> grids)> Sig=(view)=>{
  var cb=view.CropBox; var inv=cb.Transform.Inverse; var csm=view.GetCropRegionShapeManager();
  var gm=new Dictionary<long,double[]>();
  foreach(var g in new FilteredElementCollector(doc,view.Id).OfClass(typeof(Grid)).Cast<Grid>()){
    var cs=g.GetCurvesInView(DatumExtentType.ViewSpecific,view); if(cs==null||cs.Count==0)continue;
    var a=inv.OfPoint(cs[0].GetEndPoint(0)); var b=inv.OfPoint(cs[0].GetEndPoint(1));
    bool vert=Math.Abs(b.Y-a.Y)>=Math.Abs(b.X-a.X); double hi,lo;
    if(vert){hi=cb.Max.Y-Math.Max(a.Y,b.Y);lo=Math.Min(a.Y,b.Y)-cb.Min.Y;}
    else    {hi=cb.Max.X-Math.Max(a.X,b.X);lo=Math.Min(a.X,b.X)-cb.Min.X;}
    gm[g.Id.Value]=new double[]{hi,lo}; }
  return (new double[]{csm.TopAnnotationCropOffset,csm.BottomAnnotationCropOffset,
    csm.LeftAnnotationCropOffset,csm.RightAnnotationCropOffset}, gm); };
```

Build per-sector template data + the ordered target list with this **resolver** (used by
every phase; `Sig` needed only when grids are involved, otherwise drop the grid fields):

```csharp
var tAnno=new Dictionary<string,double[]>(StringComparer.OrdinalIgnoreCase);
var tTitle=new Dictionary<string,(double ox,double oy,double len,int rot)>(StringComparer.OrdinalIgnoreCase);
var tGrid=new Dictionary<string,Dictionary<long,double[]>>(StringComparer.OrdinalIgnoreCase);
var tBox=new Dictionary<string,XYZ>(StringComparer.OrdinalIgnoreCase);
var tViewId=new Dictionary<string,long>(StringComparer.OrdinalIgnoreCase);
foreach(var kv in REF){
  var sh=allSheets.FirstOrDefault(s=>s.SheetNumber.Trim().Equals(kv.Value,StringComparison.OrdinalIgnoreCase));
  if(sh==null)continue;
  foreach(var id in sh.GetAllViewports()){ var vp=doc.GetElement(id) as Viewport; if(vp==null)continue;
    var v=doc.GetElement(vp.ViewId) as View;
    if(v==null||!TARGET_TYPES.Contains(v.ViewType)||GetScopeBox(v)!=kv.Key)continue;
    tViewId[kv.Key]=v.Id.Value; var sg=Sig(v);
    tAnno[kv.Key]=sg.anno; tGrid[kv.Key]=sg.grids;
    tTitle[kv.Key]=(vp.LabelOffset.X,vp.LabelOffset.Y,vp.LabelLineLength,(int)vp.Rotation);
    var c=vp.GetBoxCenter(); tBox[kv.Key]=new XYZ(c.X,c.Y,0); break; } }
if(tBox.Count<REF.Count) return "templates incomplete: "+tBox.Count+"/"+REF.Count;
var targets=new FilteredElementCollector(doc).OfClass(typeof(View)).Cast<View>()
  .Where(v=>!v.IsTemplate&&TARGET_TYPES.Contains(v.ViewType)).Select(v=>new{v,sb=GetScopeBox(v)})
  .Where(x=>REF.ContainsKey(x.sb)&&x.v.Id.Value!=tViewId.GetValueOrDefault(x.sb,-1))
  .OrderBy(x=>x.sb,StringComparer.OrdinalIgnoreCase).ThenBy(x=>x.v.Name,StringComparer.OrdinalIgnoreCase)
  .ThenBy(x=>x.v.Id.Value).ToList();
var chunk=targets.Skip(VIEW_SKIP).Take(VIEW_TAKE).ToList();
```

---

## Phase 1 — Discover (read-only)

Preamble + grid-Sig + resolver, then:

```csharp
int annoBad=0,gridBad=0,titleBad=0,boxBad=0;
foreach(var x in chunk){ var s=Sig(x.v); var ta=tAnno[x.sb]; var tg=tGrid[x.sb];
  bool ab=false,gb=false,tb=false,bb=false;
  for(int k=0;k<4;k++) if(Math.Abs(s.anno[k]-ta[k])>ANNO_TOL) ab=true;
  foreach(var gid in tg.Keys){ if(!s.grids.ContainsKey(gid)){gb=true;continue;}
    if(Math.Max(Math.Abs(s.grids[gid][0]-tg[gid][0]),Math.Abs(s.grids[gid][1]-tg[gid][1]))>GRID_TOL) gb=true; }
  if(vpByView.TryGetValue(x.v.Id.Value,out var vp)){ var tt=tTitle[x.sb]; var rb=tBox[x.sb];
    if(Math.Abs(vp.LabelOffset.X-tt.ox)>TITLE_TOL||Math.Abs(vp.LabelOffset.Y-tt.oy)>TITLE_TOL
       ||Math.Abs(vp.LabelLineLength-tt.len)>TITLE_TOL||(int)vp.Rotation!=tt.rot) tb=true;
    var bc=vp.GetBoxCenter(); if(Math.Sqrt(Math.Pow(bc.X-rb.X,2)+Math.Pow(bc.Y-rb.Y,2))>BOX_TOL) bb=true; }
  if(ab)annoBad++; if(gb)gridBad++; if(tb)titleBad++; if(bb)boxBad++;
  if(ab||gb||tb||bb) Log($"  OFF [{x.sb}] '{x.v.Name}' anno={ab} grid={gb} title={tb} box={bb}"); }
Log($"[DISCOVER {VIEW_SKIP}-{VIEW_SKIP+chunk.Count}] anno={annoBad} grid={gridBad} title={titleBad} box={boxBad} t={sw.ElapsedMilliseconds}ms");
Log($"[NEXT] {VIEW_SKIP+VIEW_TAKE}"); return log.ToString();
```

## Phase 2 — Fix grids + annotation crop

Preamble + grid-Sig + resolver, then (maps template grid endpoints through the **target's**
own crop transform so the bubble lands at the same crop-relative spot):

```csharp
// template grid curves in template-crop-local, per sector:
var tGridCurves=new Dictionary<string,Dictionary<long,(XYZ l0,XYZ l1,bool b0,bool b1)>>(StringComparer.OrdinalIgnoreCase);
foreach(var kv in REF){ var tv=doc.GetElement(new ElementId(tViewId[kv.Key])) as View;
  var inv=tv.CropBox.Transform.Inverse; var d=new Dictionary<long,(XYZ,XYZ,bool,bool)>();
  foreach(var g in new FilteredElementCollector(doc,tv.Id).OfClass(typeof(Grid)).Cast<Grid>()){
    var cs=g.GetCurvesInView(DatumExtentType.ViewSpecific,tv); if(cs==null||cs.Count==0)continue;
    d[g.Id.Value]=(inv.OfPoint(cs[0].GetEndPoint(0)),inv.OfPoint(cs[0].GetEndPoint(1)),
      g.IsBubbleVisibleInView(DatumEnds.End0,tv),g.IsBubbleVisibleInView(DatumEnds.End1,tv)); }
  tGridCurves[kv.Key]=d; }
int annoSet=0,gridWr=0,fails=0;
foreach(var x in chunk){ var v=x.v; var xT=v.CropBox.Transform; var xInv=v.CropBox.Transform.Inverse;
  try{ var csm=v.GetCropRegionShapeManager(); var a=tAnno[x.sb];
    csm.TopAnnotationCropOffset=a[0]; csm.BottomAnnotationCropOffset=a[1];
    csm.LeftAnnotationCropOffset=a[2]; csm.RightAnnotationCropOffset=a[3]; annoSet++; }
  catch(Exception e){ fails++; Log($"[FAIL anno] '{v.Name}': {e.Message}"); }
  var gd=tGridCurves[x.sb];
  foreach(var g in new FilteredElementCollector(doc,v.Id).OfClass(typeof(Grid)).Cast<Grid>()){
    if(!gd.ContainsKey(g.Id.Value))continue;
    try{ var cur=g.GetCurvesInView(DatumExtentType.ViewSpecific,v); if(cur==null||cur.Count==0)continue;
      double lz=xInv.OfPoint(cur[0].GetEndPoint(0)).Z; var t=gd[g.Id.Value];
      var p0=xT.OfPoint(new XYZ(t.l0.X,t.l0.Y,lz)); var p1=xT.OfPoint(new XYZ(t.l1.X,t.l1.Y,lz));
      g.SetCurveInView(DatumExtentType.ViewSpecific,v,Line.CreateBound(p0,p1));
      if(g.IsBubbleVisibleInView(DatumEnds.End0,v)!=t.b0){ if(t.b0)g.ShowBubbleInView(DatumEnds.End0,v); else g.HideBubbleInView(DatumEnds.End0,v); }
      if(g.IsBubbleVisibleInView(DatumEnds.End1,v)!=t.b1){ if(t.b1)g.ShowBubbleInView(DatumEnds.End1,v); else g.HideBubbleInView(DatumEnds.End1,v); }
      gridWr++; }
    catch(Exception e){ fails++; Log($"[FAIL grid '{g.Name}'] '{v.Name}': {e.Message}"); } } }
Log($"[GRID+ANNO {VIEW_SKIP}-{VIEW_SKIP+chunk.Count}] annoSet={annoSet} gridWrites={gridWr} fails={fails} t={sw.ElapsedMilliseconds}ms");
Log($"[NEXT] {VIEW_SKIP+VIEW_TAKE}"); return log.ToString();
```

## Phase 3 — Align box centers

Preamble + resolver (grid-`Sig` not needed — you may stub it as `(view)=>(new double[4],new Dictionary<long,double[]>())`), then:

```csharp
int moved=0,unplaced=0,rot=0,fails=0;
foreach(var x in chunk){ if(!vpByView.TryGetValue(x.v.Id.Value,out var vp)){unplaced++;continue;}
  if(vp.Rotation!=ViewportRotation.None){rot++;continue;}      // rotated: skip + flag
  try{ vp.SetBoxCenter(tBox[x.sb]); moved++; }
  catch(Exception e){ fails++; Log($"[FAIL] '{x.v.Name}': {e.Message}"); } }
Log($"[ALIGN {VIEW_SKIP}-{VIEW_SKIP+chunk.Count}] moved={moved} unplaced={unplaced} rotatedSkipped={rot} fails={fails} t={sw.ElapsedMilliseconds}ms");
Log($"[NEXT] {VIEW_SKIP+VIEW_TAKE}"); return log.ToString();
```

## Phase 4 — Fix titles (LAST)

Preamble + resolver, then (line length first, offset last; skip already-correct):

```csharp
int fixedC=0,unplaced=0,fails=0;
foreach(var x in chunk){ if(!vpByView.TryGetValue(x.v.Id.Value,out var vp)){unplaced++;continue;}
  var tt=tTitle[x.sb];
  if(Math.Abs(vp.LabelOffset.X-tt.ox)<1e-3&&Math.Abs(vp.LabelOffset.Y-tt.oy)<1e-3
     &&Math.Abs(vp.LabelLineLength-tt.len)<1e-3&&(int)vp.Rotation==tt.rot) continue;
  try{ vp.LabelLineLength=tt.len;                              // FIRST
       if((int)vp.Rotation!=tt.rot)vp.Rotation=(ViewportRotation)tt.rot;
       vp.LabelOffset=new XYZ(tt.ox,tt.oy,0); fixedC++; }      // LAST
  catch(Exception e){ fails++; Log($"[FAIL] '{x.v.Name}': {e.Message}"); } }
Log($"[TITLE {VIEW_SKIP}-{VIEW_SKIP+chunk.Count}] fixed={fixedC} unplaced={unplaced} fails={fails} t={sw.ElapsedMilliseconds}ms");
Log($"[NEXT] {VIEW_SKIP+VIEW_TAKE}"); return log.ToString();
```

## Phase 5 — Verify + inline fix (post-commit, the convergent finisher)

Phase 1 is read-only; this variant also **fixes any straggler in the same pass**, with the
correct coupling handling: if the **box** is off, fix box **and** re-set the title (box move
perturbs it); if only the title is off, fix the title. Grid/anno are reported, not
auto-fixed (re-run Phase 2 for a range if they show up).

**This is a convergence loop, driven across calls:** sweep all chunks, sum the counters,
and repeat the full sweep until one reports `boxFixed=0 titleFixed=0 annoBad=0 gridBad=0`.
Fixes made in one sweep only prove out on the next (commit happens after the script
returns), which is exactly why the loop can't live inside a single script. After the first
all-zero sweep, run the pure read-only confirm below as the certification. Typical: 1–2
sweeps; not converging by ~3 means something upstream is wrong — stop and investigate.

Preamble + grid-Sig + resolver, then:

```csharp
int annoBad=0,gridBad=0,boxFixed=0,titleFixed=0;
foreach(var x in chunk){ var v=x.v; var s=Sig(v); var ta=tAnno[x.sb]; var tg=tGrid[x.sb];
  bool ab=false,gb=false; for(int k=0;k<4;k++) if(Math.Abs(s.anno[k]-ta[k])>ANNO_TOL) ab=true;
  foreach(var gid in tg.Keys){ if(!s.grids.ContainsKey(gid)){gb=true;continue;}
    if(Math.Max(Math.Abs(s.grids[gid][0]-tg[gid][0]),Math.Abs(s.grids[gid][1]-tg[gid][1]))>GRID_TOL) gb=true; }
  if(ab){annoBad++; Log($"[ANNO-OFF {x.sb}] '{v.Name}' (re-run Phase 2)");}
  if(gb){gridBad++; Log($"[GRID-OFF {x.sb}] '{v.Name}' (re-run Phase 2)");}
  if(vpByView.TryGetValue(v.Id.Value,out var vp)){ var tt=tTitle[x.sb]; var rb=tBox[x.sb];
    var bc=vp.GetBoxCenter(); bool boxOff=Math.Sqrt(Math.Pow(bc.X-rb.X,2)+Math.Pow(bc.Y-rb.Y,2))>BOX_TOL;
    bool titleOff=Math.Abs(vp.LabelOffset.X-tt.ox)>TITLE_TOL||Math.Abs(vp.LabelOffset.Y-tt.oy)>TITLE_TOL
       ||Math.Abs(vp.LabelLineLength-tt.len)>TITLE_TOL||(int)vp.Rotation!=tt.rot;
    if(boxOff){ vp.SetBoxCenter(rb);                           // box first...
      vp.LabelLineLength=tt.len; if((int)vp.Rotation!=tt.rot)vp.Rotation=(ViewportRotation)tt.rot;
      vp.LabelOffset=new XYZ(tt.ox,tt.oy,0); boxFixed++; if(titleOff)titleFixed++; }  // ...then title, always
    else if(titleOff){ vp.LabelLineLength=tt.len; if((int)vp.Rotation!=tt.rot)vp.Rotation=(ViewportRotation)tt.rot;
      vp.LabelOffset=new XYZ(tt.ox,tt.oy,0); titleFixed++; } } }
Log($"[VERIFY {VIEW_SKIP}-{VIEW_SKIP+chunk.Count}] annoBad={annoBad} gridBad={gridBad} boxFixed={boxFixed} titleFixed={titleFixed} t={sw.ElapsedMilliseconds}ms");
Log($"[NEXT] {VIEW_SKIP+VIEW_TAKE}"); return log.ToString();
```

**Pure read-only confirm** (for the final measured 0-off certification): same as above but
delete the two `vp.Set…` fix branches and just count `boxBad`/`titleBad`. Run it after a
full Phase-5 pass reports 0 fixes.
