import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
HERE=Path(__file__).resolve().parent
packaged=(HERE/'input-predictions.json').exists()
data=json.loads((HERE/'input-predictions.json' if packaged else ROOT/'outputs/SpeciesZoomShadow/review/results.json').read_text())
manifest=json.loads((HERE/'reviewed-manifest.json' if packaged else ROOT/'work/reviewed-manifest.json').read_text())
samples=[r for r in manifest if r['split']=='test' and r['camera']=='Water Bowl']
variants={k:{r['detection_id']:r for r in v['records']} for k,v in data['variants'].items()}

def iou(a,b):
    area=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return area/((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-area+1e-9)

def combine(did,keys,minimum):
    proposals=[]
    for key in keys:
        for p in variants[key][did]['cats']:
            # Distinct processing variants, never duplicate boxes within one variant.
            support=[k for k in keys if any(iou(p['box'],q['box'])>=.35 for q in variants[k][did]['cats'])]
            if len(support)>=minimum:
                proposals.append(dict(p,variants=support))
    # Keep distinct supported boxes for target recall; report the effect of
    # conventional deduplication separately instead of hiding that loss.
    distinct=[]
    for p in proposals:
        if not any(p['box']==q['box'] for q in distinct):distinct.append(p)
    return distinct

def dedup(proposals):
    kept=[]
    for p in sorted(proposals,key=lambda p:p['score'],reverse=True):
        if all(iou(p['box'],q['box'])<.35 for q in kept):kept.append(p)
    return kept

# Fixed exploratory rules, no score/overlap threshold sweep or selection by ground truth.
rules=[(k,[k],1) for k in variants]+[
    ('original_or_shadow',['original320','shadow320'],1),
    ('original_and_shadow',['original320','shadow320'],2),
    ('any_of_four',list(variants),1),
    ('two_of_four',list(variants),2),
    ('three_of_four',list(variants),3)]
report={'camera':'Water Bowl','scope':'Development only; correlated variants of one checkpoint, not independent votes. Previously inspected contextual crops; not automatic full-frame discovery.','protocol':{'confidence':.30,'support_iou':.35,'target_match_iou':.50,'dedup_iou':.35},'sessions':sorted({r['session'] for r in samples}),'rules':{}}
for name,keys,minimum in rules:
    records=[]
    for r in samples:
        boxes=combine(r['detection_id'],keys,minimum)
        x,y,_,_=r['source_region'];a,b,c,d=r['box'];gt=[a-x,b-y,c-x,d-y]
        matched=r['kind']=='Cat' and any(iou(p['box'],gt)>=.50 for p in boxes)
        merged=dedup(boxes)
        merged_match=r['kind']=='Cat' and any(iou(p['box'],gt)>=.50 for p in merged)
        records.append({'clip':r['clip'],'detection_id':r['detection_id'],'kind':r['kind'],'matched':matched,'matched_after_dedup':merged_match,'predictions':boxes})
    summary={'cat_targets':sum(r['kind']=='Cat' for r in records),'cats_found':sum(r['matched'] for r in records),'cats_found_after_dedup':sum(r['matched_after_dedup'] for r in records),'noncat_images':sum(r['kind']!='Cat' for r in records),'noncat_false_images':sum(r['kind']!='Cat' and bool(r['predictions']) for r in records),'missed_cat_clips':[r['clip'] for r in records if r['kind']=='Cat' and not r['matched']]}
    report['rules'][name]={'summary':summary,'records':records}
    print(name,json.dumps(summary))
out=HERE if packaged else ROOT/'outputs/WaterBowlCombined';out.mkdir(exist_ok=True)
(out/'results.json').write_text(json.dumps(report,indent=2))
print('Sessions:',report['sessions'])
