#!/usr/bin/env python3
"""Exercise publication through an isolated Git repository and scripted gh executable."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release", ROOT / "scripts/publish-skill-release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

FAKE_GH = r'''
import base64,json,os,subprocess,sys
from pathlib import Path
p=Path(os.environ['FAKE_GH_STATE']); s=json.loads(p.read_text()); a=sys.argv[1:]
s['calls'].append(a)
def save(): p.write_text(json.dumps(s))
def fail(message):
 save(); print(message,file=sys.stderr); sys.exit(1)
def answer(value):
 save(); print(json.dumps(value)); sys.exit()
if a[0]=='api':
 endpoint=a[1]
 if '/releases/tags/' in endpoint:
  if s.get('auth_failure'): fail('gh: Bad credentials (HTTP 401)')
  if s['release'] is None or s['release']['draft']: fail('gh: Not Found (HTTP 404)')
  answer(s['release'])
 if endpoint.endswith('/releases'):
  assert '--paginate' in a and '--slurp' in a
  if s.get('list_failure'): fail('gh: Forbidden (HTTP 403)')
  matches=[] if s['release'] is None else [s['release']]
  answer([[{'id':99,'tag_name':'v9.0.0'}],matches + (matches if s.get('duplicate_release') else [])])
 if endpoint.endswith('/assets'):
  answer([[{'id':v['id'],'name':k} for k,v in s['assets'].items()]] + ([[{'id':1,'name':next(iter(s['assets']))}]] if s.get('duplicate') else []))
 if '/releases/assets/' in endpoint:
  asset_id=endpoint.rsplit('/',1)[1]
  name=next(k for k,v in s['assets'].items() if str(v['id'])==asset_id)
  if '--method' in a:
   assert a[a.index('--method')+1]=='DELETE'
   assert name.endswith(('-skill.zip','-build.json','-SHA256SUMS.txt'))
   if s.get('delete_failure_after')==s.get('deleted',0): fail('delete interrupted')
   del s['assets'][name]; s['deleted']=s.get('deleted',0)+1; save(); sys.exit()
  if s.get('download_failure'): fail('download unavailable')
  data=s['assets'][name]['data']
  if s.get('plugin_download_failure') and name.endswith('-codex-plugin.zip'): fail('plugin unavailable')
  if s.get('tag_drift'):
   subprocess.run(['git','--git-dir',s['remote'],'update-ref','refs/tags/v0.1.0',s['other_sha']],check=True)
  save(); sys.stdout.buffer.write(b'wrong' if s.get('download_mismatch') else base64.b64decode(data)); sys.exit()
if a[:2]==['release','create']:
 assert '--draft' in a and '--verify-tag' in a
 s['release']={'id':42,'tag_name':a[2],'draft':True,'prerelease':False,'immutable':False,'body':Path(a[a.index('--notes-file')+1]).read_text(),'html_url':'https://example.test/release'}
 save(); sys.exit()
if a[:2]==['release','upload']:
 assert '--clobber' not in a
 if s.get('upload_failure_after') == len(s['assets']): fail('upload interrupted')
 f=Path(a[3]); assert f.name not in s['assets']
 s['assets'][f.name]={'id':max([v['id'] for v in s['assets'].values()]+[0])+1,'data':base64.b64encode(f.read_bytes()).decode()}
 if s.get('legacy_id_drift'):
  for name,asset in s['assets'].items():
   if name.endswith('-skill.zip'): asset['id']+=100
 if s.get('release_id_drift'): s['release']['id']+=1
 save(); sys.exit()
if a[:2]==['release','edit']:
 if '--notes-file' in a:
  if s.get('notes_failure'): fail('notes interrupted')
  s['release']['body']=Path(a[a.index('--notes-file')+1]).read_text(); save(); sys.exit()
 assert '--draft=false' in a and '--verify-tag' in a
 s['release']['draft']=False; save(); sys.exit()
fail('unexpected gh command: '+repr(a))
'''


class Publication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="agent-flow-release-test-")
        cls.base = Path(cls.temp.name).resolve()
        cls.source = cls.base / "source"
        shutil.copytree(ROOT / "skills/agent-flow", cls.source / "skills/agent-flow",
                        ignore=shutil.ignore_patterns("__pycache__", "*.bak"))
        shutil.copytree(ROOT / ".codex-plugin", cls.source / ".codex-plugin")
        for name in ("README.md", "README.ru.md", "LICENSE"):
            shutil.copyfile(ROOT / name, cls.source / name)
        cls.git("init", "-b", "main")
        cls.git("config", "user.name", "Release fixture")
        cls.git("config", "user.email", "fixture@example.invalid")
        cls.git("add", ".")
        cls.git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")
        cls.sha = cls.git("rev-parse", "HEAD")
        cls.git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-qm", "later main")
        cls.other_sha = cls.git("rev-parse", "HEAD")
        cls.git("tag", "-a", "v0.1.0", cls.sha, "-m", "annotated release")
        cls.remote = cls.base / "remote.git"
        subprocess.run(["git", "clone", "--bare", str(cls.source), str(cls.remote)], check=True,
                       capture_output=True)
        cls.git("remote", "add", "origin", str(cls.remote))
        cls.git("checkout", "--detach", cls.sha)
        cls.bundle = cls.base / "bundle"
        release.verifier.builder.build(cls.source, cls.bundle, cls.sha, release_tag="v0.1.0")
        cls.bin = cls.base / "bin"
        cls.bin.mkdir()
        gh = cls.bin / "gh"
        gh.write_text(f"#!{sys.executable}\n" + FAKE_GH)
        gh.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def git(cls, *args):
        result = subprocess.run(["git", *args], cwd=cls.source, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def setUp(self):
        self.state_path = self.base / "state.json"
        self.backup = self.base / ("backup-" + self.id().split(".")[-1])
        self.state = {"release": None, "assets": {}, "calls": [], "remote": str(self.remote),
                      "other_sha": self.other_sha}
        self.save()
        subprocess.run(["git", "--git-dir", str(self.remote), "update-ref", "refs/tags/v0.1.0",
                        self.git("rev-parse", "v0.1.0")], check=True)
        subprocess.run(["git", "--git-dir", str(self.remote), "update-ref", "refs/heads/main",
                        self.other_sha], check=True)

    def save(self):
        self.state_path.write_text(json.dumps(self.state))

    def invoke(self, success=True, tag="v0.1.0", sha=None, source_only=False, replacement=False):
        args = [sys.executable, "-B", str(ROOT / "scripts/publish-skill-release.py"),
                "--source", str(self.source), "--release-tag", tag, "--commit-sha", sha or self.sha]
        args += ["--check-source-only"] if source_only else ["--directory", str(self.bundle), "--repository", "owner/repo"]
        if replacement:
            args += ["--replace-skill-release", "--backup-directory", str(self.backup)]
        env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
               "FAKE_GH_STATE": str(self.state_path), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run(args, env=env, text=True, capture_output=True)
        self.state = json.loads(self.state_path.read_text())
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def edits(self):
        return [a for a in self.state['calls'] if a[:2] == ['release', 'edit']]

    def test_publish_and_repeat_without_mutation(self):
        self.invoke()
        self.assertFalse(self.state['release']['draft'])
        self.assertEqual(set(self.state['assets']), {'agent-flow-0.1.0-codex-plugin.zip'})
        calls = self.state['calls']
        publish_index = next(i for i,a in enumerate(calls) if a[:2]==['release','edit'])
        self.assertEqual(sum('/releases/assets/' in a[1] for a in calls[:publish_index] if a[0]=='api'), 1)
        self.state['calls'] = []; self.save()
        self.invoke()
        self.assertTrue(all(a[0]=='api' for a in self.state['calls']))
        print('release-draft-verified-before-publish release-existing-identical-no-mutation')

    def test_upload_failure_and_resume(self):
        self.state['upload_failure_after'] = 0; self.save()
        self.invoke(False)
        self.assertTrue(self.state['release']['draft']); self.assertFalse(self.edits())
        self.assertEqual(len(self.state['assets']), 0)
        self.state.pop('upload_failure_after'); self.save()
        self.invoke()
        self.assertFalse(self.state['release']['draft'])
        print('release-upload-failure-stays-draft release-owned-draft-resume-safe')

    def test_auth_failure_does_not_create(self):
        self.state['auth_failure'] = True; self.save(); self.invoke(False)
        self.assertIsNone(self.state['release'])
        self.assertFalse(any(a[0]=='release' for a in self.state['calls']))
        print('release-auth-failure-no-create')

    def test_draft_listing_failure_or_ambiguity_never_writes(self):
        self.state['list_failure'] = True; self.save(); self.invoke(False)
        self.assertFalse(any(a[0] == 'release' for a in self.state['calls']))
        self.state.pop('list_failure'); self.save(); self.invoke()
        self.state['release']['draft'] = True
        self.state['duplicate_release'] = True
        self.state['calls'] = []; self.save(); self.invoke(False)
        self.assertFalse(any(a[0] == 'release' for a in self.state['calls']))
        print('release-draft-list-failure-no-create release-duplicate-draft-no-mutation')

    def test_conflicting_release_and_assets(self):
        self.invoke()
        self.state['release']['body'] = 'someone else'; self.state['calls']=[]; self.save()
        self.invoke(False); self.assertFalse(self.edits())
        print('release-existing-conflict-rejected')

    def test_extra_duplicate_corrupt_or_unreadable_assets_never_publish(self):
        self.invoke()
        initial = json.loads(json.dumps(self.state))
        for failure in ('extra', 'duplicate', 'download_mismatch', 'download_failure'):
            with self.subTest(failure=failure):
                self.state=json.loads(json.dumps(initial)); self.state['release']['draft']=True; self.state['calls']=[]
                if failure=='extra': self.state['assets']['unexpected.txt']={'id':50,'data':''}
                else: self.state[failure]=True
                self.save(); self.invoke(False); self.assertFalse(self.edits())
                self.assertTrue(self.state['release']['draft'])
        print('release-unexpected-asset-rejected release-download-mismatch-no-publish')

    def test_remote_tag_drift_stays_draft(self):
        self.state['tag_drift']=True; self.save(); self.invoke(False)
        self.assertTrue(self.state['release']['draft']); self.assertFalse(self.edits())
        print('release-remote-tag-drift-rejected')

    def legacy_release(self):
        self.state['release'] = {
            'id': 42, 'tag_name': 'v0.1.0', 'draft': False, 'prerelease': False, 'immutable': False,
            'body': f'<!-- agent-flow-skill-release v0.1.0 {self.sha} -->',
            'html_url': 'https://example.test/release'}
        self.legacy = release.legacy_skill_files(self.source, 'v0.1.0', self.sha)
        self.state['assets'] = {name: {'id': index + 1, 'data': base64.b64encode(data).decode()}
                                for index, (name, data) in enumerate(self.legacy.items())}
        self.save()

    def deletions(self):
        return [a for a in self.state['calls'] if '--method' in a and 'DELETE' in a]

    def test_replacement_backups_downloads_before_deletion_and_readonly_repeat(self):
        self.legacy_release()
        self.invoke(False)
        self.assertFalse(self.deletions())
        self.invoke(replacement=True)
        self.assertEqual(set(self.state['assets']), {'agent-flow-0.1.0-codex-plugin.zip'})
        self.assertEqual({p.name: p.read_bytes() for p in self.backup.iterdir()}, self.legacy)
        calls = self.state['calls']
        upload = next(i for i, a in enumerate(calls) if a[:2] == ['release', 'upload'])
        delete = next(i for i, a in enumerate(calls) if '--method' in a)
        self.assertTrue(any('/releases/assets/4' in a[1] for a in calls[upload:delete] if a[0]=='api'))
        self.assertEqual(len(self.deletions()), 3)
        self.assertIn('agent-flow-plugin-release', self.state['release']['body'])
        self.assertFalse(self.state['release']['draft'])
        self.state['calls'] = []; self.save()
        self.invoke(replacement=True); self.invoke()
        self.assertTrue(all(a[0]=='api' and '--method' not in a for a in self.state['calls']))
        print('migration-backup-verified migration-download-before-delete migration-plugin-only '
              'migration-repeat-readonly')

    def test_replacement_resumes_after_each_deletion_and_notes_failure(self):
        for stopped in range(4):
            with self.subTest(stopped=stopped):
                self.legacy_release()
                self.state['calls'] = []
                self.state.pop('deleted', None)
                self.state.pop('delete_failure_after', None)
                if stopped < 3:
                    self.state['delete_failure_after'] = stopped
                else:
                    self.state['notes_failure'] = True
                self.save(); self.invoke(False, replacement=True)
                self.assertIn('agent-flow-0.1.0-codex-plugin.zip', self.state['assets'])
                self.assertIn('agent-flow-skill-release', self.state['release']['body'])
                self.state.pop('delete_failure_after', None)
                self.state.pop('notes_failure', None)
                self.save(); self.invoke(replacement=True)
                self.assertEqual(len(self.state['assets']), 1)
        print('migration-partial-delete-resume migration-notes-failure-resume')

    def test_replacement_rejects_invalid_remote_state_before_writes(self):
        for failure in ('foreign-marker','extra','duplicate','corrupt','missing','draft','prerelease','immutable'):
            with self.subTest(failure=failure):
                self.legacy_release(); self.state['calls'] = []; self.state.pop('duplicate', None)
                if failure == 'foreign-marker': self.state['release']['body'] = 'other'
                elif failure == 'extra': self.state['assets']['unknown.zip'] = {'id': 99, 'data': ''}
                elif failure == 'corrupt': self.state['assets'][next(iter(self.legacy))]['data'] = ''
                elif failure == 'missing': self.state['assets'].pop(next(iter(self.legacy)))
                elif failure == 'duplicate': self.state['duplicate'] = True
                else: self.state['release'][failure] = True
                self.save(); self.invoke(False, replacement=True)
                self.assertFalse(any(a[0]=='release' for a in self.state['calls']))
                self.assertFalse(self.deletions())
        print('migration-invalid-state-no-write')

    def test_replacement_upload_or_verification_failure_never_deletes(self):
        for failure in ('upload_failure_after', 'plugin_download_failure', 'legacy_id_drift',
                        'release_id_drift', 'tag_drift'):
            with self.subTest(failure=failure):
                self.legacy_release(); self.state['calls'] = []
                self.state[failure] = 3 if failure == 'upload_failure_after' else True
                self.save(); self.invoke(False, replacement=True)
                self.assertFalse(self.deletions())
                self.assertTrue(self.legacy.keys() <= self.state['assets'].keys())
                self.state.pop(failure)
                subprocess.run(['git','--git-dir',str(self.remote),'update-ref','refs/tags/v0.1.0',
                                self.git('rev-parse','v0.1.0')],check=True)
        print('migration-upload-or-download-failure-no-delete migration-asset-id-drift-rejected '
              'migration-source-drift-rejected')

    def test_malformed_release_flags_and_ids_are_rejected(self):
        self.legacy_release()
        valid = self.state['release']
        for field in ('draft', 'prerelease', 'immutable', 'id'):
            for value in ('missing', None, 0, 1, 'false'):
                if field == 'id' and value == 1:
                    value = True
                with self.subTest(field=field, value=value):
                    malformed = dict(valid)
                    if value == 'missing': malformed.pop(field)
                    else: malformed[field] = value
                    with self.assertRaises(release.PackageError):
                        release.check_release_identity(malformed, 'v0.1.0', valid['body'])
        for invalid_id in (None, 0, -1, True, '1'):
            github = release.GitHub('owner/repo')
            github.assets = lambda _: [{'id': invalid_id, 'name': 'plugin.zip'}]
            with self.assertRaises(release.PackageError):
                github.verify_assets(valid, {'plugin.zip': b''}, complete=True)
        print('release-malformed-flags-and-ids-rejected')

    def test_replacement_backup_conflict_or_missing_resume_backup_never_deletes(self):
        self.legacy_release()
        self.backup.mkdir()
        target = self.backup / next(iter(self.legacy))
        target.write_bytes(b'preserve user backup')
        self.invoke(False, replacement=True)
        self.assertFalse(self.deletions())
        self.assertEqual(target.read_bytes(), b'preserve user backup')
        target.unlink()
        target.symlink_to(self.bundle / 'agent-flow-0.1.0-codex-plugin.zip')
        self.invoke(False, replacement=True)
        self.assertFalse(self.deletions())
        target.unlink()
        self.state['delete_failure_after'] = 1; self.save()
        self.invoke(False, replacement=True)
        deleted_name = next(name for name in self.legacy if name not in self.state['assets'])
        (self.backup / deleted_name).unlink()
        self.state.pop('delete_failure_after'); self.state['calls'] = []; self.save()
        self.invoke(False, replacement=True)
        self.assertFalse(self.deletions())
        print('migration-backup-conflict-rejected migration-backup-symlink-rejected '
              'migration-missing-backup-no-delete')

    def test_tag_and_source_gates(self):
        self.invoke(source_only=True)
        print('release-tag-match-ok')
        for tag in ('vbad','v00.1.0','v0.1.0-rc.1','v0.1.0+build'):
            self.invoke(False, tag=tag, source_only=True)
        print('release-tag-invalid-rejected')
        self.invoke(False, tag='v0.2.0', source_only=True)
        print('release-tag-mismatch-rejected')
        self.invoke(False, sha='0'*40, source_only=True)
        print('release-source-sha-rejected')
        tree=self.git('rev-parse', 'HEAD^{tree}')
        orphan=self.git('commit-tree', tree, '-m', 'unrelated main')
        subprocess.run(['git','push','--force','origin',f'{orphan}:refs/heads/main'],cwd=self.source,
                       check=True,capture_output=True)
        self.invoke(False,source_only=True)
        print('release-unmerged-commit-rejected')


class Workflow(unittest.TestCase):
    @staticmethod
    def validate(text):
        def require(value):
            if not value:
                raise ValueError('unsafe release workflow')
        require('on:\n  push:\n\npermissions:\n  contents: read\n' in text)
        checks, publish = text.split('  publish-skill-release:\n')
        require('contents: write' not in checks)
        require('branches:' not in checks and 'paths:' not in checks)
        require('cancel-in-progress: true' not in text)
        require('needs: check-and-build' in publish and 'contents: write' in publish)
        tag_condition="github.ref_type == 'tag' && startsWith(github.ref_name, 'v')"
        require(tag_condition in checks and tag_condition in publish)
        require('ref: ${{ github.sha }}' in checks and 'ref: ${{ github.sha }}' in publish)
        require('name: ${{ needs.check-and-build.outputs.release-artifact }}' in publish)
        require('persist-credentials: false' in checks and 'persist-credentials: false' in publish)
        require('cancel-in-progress: false' in publish and 'group: skill-release-${{ github.ref }}' in publish)
        suite=checks.index('/opt/homebrew/opt/agent-flow-checks/bin/python3 scripts/check-all.py')
        build=checks.index('python3 scripts/build-distributions.py')
        stable=checks.index('python3 scripts/publish-skill-release.py --check-source-only')
        upload=checks.index('uses: actions/upload-artifact@')
        require(suite < build < stable < upload)
        require('continue-on-error' not in text and 'always()' not in text)
        require('python3 scripts/publish-skill-release.py' in publish)

    def test_workflow_and_negative_mutations(self):
        text=(ROOT/'.github/workflows/push-build.yml').read_text()
        self.validate(text)
        mutations=[
            text.replace('contents: read','contents: write',1),
            text.replace('needs: check-and-build','needs: other-job'),
            text.replace("github.ref_type == 'tag'", "github.ref_type == 'branch'"),
            text.replace('cancel-in-progress: false','cancel-in-progress: true'),
            text.replace('python3 scripts/check-all.py','python3 scripts/some-tests.py'),
            text.replace('  push:\n','  push:\n    branches: [main]\n'),
            text.replace('scripts/check-all.py', 'SWAP').replace('scripts/build-distributions.py',
                'scripts/check-all.py').replace('SWAP', 'scripts/build-distributions.py'),
        ]
        for mutation in mutations:
            with self.assertRaises(ValueError): self.validate(mutation)
        print('release-after-full-check-all release-write-permission-isolated '
              'release-branch-push-does-not-publish release-workflow-drift-rejected')

    def test_zip_docs_and_readme_entry(self):
        for lang in ('en','ru'):
            docs=(ROOT/f'skills/agent-flow/docs/{lang}/installation.md').read_text()
            heading = '## Codex plugin' if lang == 'en' else '## Плагин Codex'
            section=docs.split(heading)[1].split('\n## ')[0]
            self.assertIn('releases/latest',section)
            self.assertIn('agent-flow-X.Y.Z-codex-plugin.zip',section)
            self.assertIn('codex plugin add agent-flow@agent-flow',section)
            self.assertNotIn('npx',section)
            self.assertIn('check-installed-package.py',section)
            self.assertIn('npx skills add https://github.com/svishniakov/agent-flow',docs)
            self.assertNotIn('agent-flow-X.Y.Z-skill.zip',docs)
        for name in ('README.md','README.ru.md'):
            text=(ROOT/name).read_text()
            self.assertIn('releases/latest',text)
            self.assertIn('agent-flow-X.Y.Z-codex-plugin.zip',text)
            self.assertNotIn('agent-flow-X.Y.Z-skill.zip',text)
        print('release-docs-two-install-paths')


if __name__ == '__main__':
    unittest.main(verbosity=2)
