#!/usr/bin/env python3
"""Reversible, device-local fork-compatible Caelestia plugin rollout.

prepare: build immutable per-device candidates by layering clean migration files
         over a full copy of current installed plugin trees.
activate: atomically exchange plugin directory entries; never delete originals.
rollback: restore originals from the transaction journal (safe after interruption).
"""
import argparse, datetime, hashlib, json, os, pathlib, shutil, subprocess, sys, tarfile, uuid
HOME=pathlib.Path.home()
LIVE=HOME/'.local/share/caelestia/plugins'
BASE=HOME/'.local/state/caelestia/native-plugin-migration'
ID='fork-32-tracked-20261021'
SRC=BASE/ID
STAMP='fork-deploy-20261021'
RELEASE=BASE/(STAMP+'-candidate')
BACKUP=BASE/(STAMP+'-backup')
JOURNAL=BACKUP/'transaction.json'
def run(args,cwd=None):
    return subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
def dump(path,obj):
    tmp=path.with_name(path.name+'.partial')
    tmp.write_text(json.dumps(obj,indent=2)+'\n')
    os.chmod(tmp,0o600)
    os.replace(tmp,path)
def identity(path):
    m=json.loads((path/'manifest.json').read_text())
    return (m['author']+'/'+m['name']).lower()
def dirty_names(root):
    proc=subprocess.run(['git','-C',str(root),'status','--porcelain','-z'],
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if proc.returncode: return set()
    parts=proc.stdout.decode(errors='replace').split('\0')
    dirty=set()
    idx=0
    while idx<len(parts):
        row=parts[idx]
        idx+=1
        if not row:continue
        rel=row[3:]
        dirty.add(rel)
        if row[:2].startswith('R') or row[:2].endswith('R') or 'C' in row[:2]:
            if idx<len(parts):dirty.add(parts[idx]);idx+=1
    return dirty
def validate(root):
    j=json.loads((SRC/'RELEASE_MANIFEST.json').read_text())
    assert j['count']==32 and j['profile']=='fork'
    manifests={}
    for d in SRC.iterdir():
        if d.is_dir() and (d/'manifest.json').is_file():
            key=identity(d)
            if key in manifests: raise RuntimeError('duplicate candidate '+key)
            manifests[key]=d
    assert len(manifests)==32
    for p in j['plugins']:
        d=manifests[p['plugin_id']]
        assert hashlib.sha256((d/'manifest.json').read_bytes()).hexdigest()==p['manifest_sha256']
    return manifests
def prepare():
    if RELEASE.exists() or BACKUP.exists():
        raise RuntimeError('Existing release/backup; refusing to overwrite')
    if not SRC.is_dir():raise RuntimeError('candidate stage absent')
    candidates=validate(SRC)
    installed={}
    for path in LIVE.iterdir():
        if path.is_dir() and (path/'manifest.json').is_file():
            key=identity(path)
            if key in candidates:
                if key in installed:raise RuntimeError('duplicate installed plugin '+key)
                installed[key]=path
    if len(installed)!=32:raise RuntimeError(f'expected 32 installed candidates, found {len(installed)}')
    RELEASE.mkdir(mode=0o700,parents=True)
    changes=[]
    try:
        for key in sorted(candidates):
            old=installed[key]
            cand=candidates[key]
            dest=RELEASE/old.name
            dirty=dirty_names(old)
            shutil.copytree(old,dest,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'),dirs_exist_ok=False)
            written,kept=0,[]
            for src in cand.rglob('*'):
                if not src.is_file() or src.is_symlink():continue
                rel=src.relative_to(cand)
                if any(x in ('__pycache__','.git') for x in rel.parts):continue
                target=dest/rel
                if str(rel) in dirty:
                    kept.append(str(rel));continue
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.is_symlink() or (target.exists() and target.is_dir()):
                    raise RuntimeError('Unsafe destination '+str(target))
                if not target.exists() or target.read_bytes()!=src.read_bytes():
                    shutil.copy2(src,target)
                    written+=1
            assert identity(dest)==key,(key,'identity changed')
            changes.append(dict(key=key,name=old.name,original=str(old.resolve()),
                                was_symlink=old.is_symlink(),
                                symlink_target=os.readlink(old) if old.is_symlink() else None,
                                installed_sha=hashlib.sha256((old/'manifest.json').read_bytes()).hexdigest(),
                                candidate_sha=hashlib.sha256((dest/'manifest.json').read_bytes()).hexdigest(),
                                changed_clean_files=written,preserved_local_files=kept))
        # Check every QML file without writing back to candidates.
        failures=[]
        for file in RELEASE.rglob('*.qml'):
            r=run(['/usr/lib/qt6/bin/qmlformat',str(file)])
            if r.returncode:failures.append(str(file.relative_to(RELEASE)))
        if failures:raise RuntimeError('QML syntax errors: '+str(failures[:10]))
        report=dict(host=os.uname().nodename,release=str(RELEASE),backup=str(BACKUP),
                    count=32,prepared_at=datetime.datetime.now().isoformat(),
                    activated=False,entries=changes,qml_verified=True,
                    total_candidate_modifications=sum(x['changed_clean_files'] for x in changes),
                    protected_local_files=sum(len(x['preserved_local_files']) for x in changes))
        dump(RELEASE/'release-plan.json',report)
        print(json.dumps({k:report[k] for k in ('host','count','qml_verified','total_candidate_modifications','protected_local_files')},indent=2))
    except Exception:
        # Stage is private, but retain it for investigation if any validation fails.
        raise
def preflight(plan):
    for e in plan['entries']:
        old=LIVE/e['name']
        new=RELEASE/e['name']
        if not (old.exists() and new.exists()):raise RuntimeError('live or candidate missing '+e['name'])
        if identity(old)!=e['key'] or identity(new)!=e['key']:raise RuntimeError('plugin identity drift '+e['key'])
        if old.is_symlink()!=e['was_symlink']:raise RuntimeError('link type changed '+e['key'])
        if old.is_symlink() and os.readlink(old)!=e['symlink_target']:raise RuntimeError('link moved '+e['key'])
        if hashlib.sha256((old/'manifest.json').read_bytes()).hexdigest()!=e['installed_sha']:
            raise RuntimeError('manifest changed since prepare '+e['key'])
def activate():
    if JOURNAL.exists():raise RuntimeError('Existing transaction; refusing second activation')
    plan=json.loads((RELEASE/'release-plan.json').read_text())
    assert len(plan['entries'])==32 and plan['host']==os.uname().nodename
    preflight(plan)
    BACKUP.mkdir(mode=0o700,parents=True)
    # Shell and configuration are not modified, but back up their current content as metadata.
    config=HOME/'.config/caelestia/plugins.json'
    if config.exists():shutil.copy2(config,BACKUP/'plugins.json')
    shell=HOME/'.config/quickshell/caelestia'
    r=run(['git','-C',str(shell),'status','--porcelain'])
    (BACKUP/'shell-git-status.txt').write_text(r.stdout)
    # A transaction journal is written before first mutation so it can be recovered after interruption.
    txn=dict(host=os.uname().nodename,created=datetime.datetime.now().isoformat(),
             status='in_progress',swapped=[],entries=plan['entries'])
    dump(JOURNAL,txn)
    try:
        for entry in plan['entries']:
            name=entry['name']
            old=LIVE/name
            saved=BACKUP/name
            os.replace(old,saved)
            try:os.symlink(RELEASE/name,old)
            except Exception:
                os.replace(saved,old)
                raise
            txn['swapped'].append(name)
            dump(JOURNAL,txn)
        txn['status']='activated_pending_health'
        dump(JOURNAL,txn)
        print('ACTIVATED',len(txn['swapped']))
    except Exception:
        rollback()
        raise
def rollback():
    if not JOURNAL.exists():raise RuntimeError('No transaction to roll back')
    txn=json.loads(JOURNAL.read_text())
    for name in reversed([e['name'] for e in txn['entries']]):
        old=LIVE/name
        saved=BACKUP/name
        if saved.exists() or saved.is_symlink():
            if old.is_symlink() and pathlib.Path(os.readlink(old))==RELEASE/name:
                old.unlink()
            elif old.exists():raise RuntimeError('Rollback blocked by unexpected active content '+name)
            os.replace(saved,old)
    txn['status']='rolled_back'
    dump(JOURNAL,txn)
    print('ROLLED_BACK',len(txn['entries']))
def guard():
    """Automatic timer callback: recover only unfinished/unconfirmed switches."""
    if not JOURNAL.exists():
        print('NO_TRANSACTION')
        return
    txn=json.loads(JOURNAL.read_text())
    if txn.get('status') in {'in_progress','activated_pending_health'}:
        rollback()
    else:
        print('NO_ROLLBACK_REQUIRED',txn.get('status'))
def status():
    if not JOURNAL.exists():
        print('NOT_ACTIVATED')
        return
    txn=json.loads(JOURNAL.read_text())
    good=sum((LIVE/e['name']).is_symlink() and
             os.readlink(LIVE/e['name'])==str(RELEASE/e['name']) for e in txn['entries'])
    print(json.dumps({'status':txn['status'],'active_migration_links':good,'total':len(txn['entries'])}))
def confirm():
    txn=json.loads(JOURNAL.read_text())
    if txn['status']!='activated_pending_health':raise RuntimeError('Not pending health')
    if sum((LIVE/e['name']).is_symlink() and
           os.readlink(LIVE/e['name'])==str(RELEASE/e['name']) for e in txn['entries'])!=32:raise RuntimeError('Link validation failed')
    txn['status']='active_verified'
    dump(JOURNAL,txn)
    print('CONFIRMED')
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['prepare','activate','rollback','status','confirm','guard'])
    args=parser.parse_args()
    globals()[args.command]()
