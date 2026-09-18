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
  if s.get('download_failure'): fail('download unavailable')
  data=next(v['data'] for v in s['assets'].values() if str(v['id'])==endpoint.rsplit('/',1)[1])
  if s.get('tag_drift'):
   subprocess.run(['git','--git-dir',s['remote'],'update-ref','refs/tags/v0.1.0',s['other_sha']],check=True)
  save(); sys.stdout.buffer.write(b'wrong' if s.get('download_mismatch') else base64.b64decode(data)); sys.exit()
if a[:2]==['release','create']:
 assert '--draft' in a and '--verify-tag' in a
 s['release']={'id':42,'tag_name':a[2],'draft':True,'prerelease':False,'body':Path(a[a.index('--notes-file')+1]).read_text(),'html_url':'https://example.test/release'}
 save(); sys.exit()
if a[:2]==['release','upload']:
 assert '--clobber' not in a
 if s.get('upload_failure_after') == len(s['assets']): fail('upload interrupted')
 f=Path(a[3]); assert f.name not in s['assets']
 s['assets'][f.name]={'id':len(s['assets'])+1,'data':base64.b64encode(f.read_bytes()).decode()}
 save(); sys.exit()
if a[:2]==['release','edit']:
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
        self.state = {"release": None, "assets": {}, "calls": [], "remote": str(self.remote),
                      "other_sha": self.other_sha}
        self.save()
        subprocess.run(["git", "--git-dir", str(self.remote), "update-ref", "refs/tags/v0.1.0",
                        self.git("rev-parse", "v0.1.0")], check=True)
        subprocess.run(["git", "--git-dir", str(self.remote), "update-ref", "refs/heads/main",
                        self.other_sha], check=True)

    def save(self):
        self.state_path.write_text(json.dumps(self.state))

    def invoke(self, success=True, tag="v0.1.0", sha=None, source_only=False):
        args = [sys.executable, "-B", str(ROOT / "scripts/publish-skill-release.py"),
                "--source", str(self.source), "--release-tag", tag, "--commit-sha", sha or self.sha]
        args += ["--check-source-only"] if source_only else ["--directory", str(self.bundle), "--repository", "owner/repo"]
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
        self.assertEqual(len(self.state['assets']), 3)
        calls = self.state['calls']
        publish_index = next(i for i,a in enumerate(calls) if a[:2]==['release','edit'])
        self.assertEqual(sum('/releases/assets/' in a[1] for a in calls[:publish_index] if a[0]=='api'), 3)
        self.state['calls'] = []; self.save()
        self.invoke()
        self.assertTrue(all(a[0]=='api' for a in self.state['calls']))
        print('release-draft-verified-before-publish release-existing-identical-no-mutation')

    def test_upload_failure_and_resume(self):
        self.state['upload_failure_after'] = 1; self.save()
        self.invoke(False)
        self.assertTrue(self.state['release']['draft']); self.assertFalse(self.edits())
        self.assertEqual(len(self.state['assets']), 1)
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
            section=docs.split('## ')[1]
            self.assertIn('releases/latest',section)
            self.assertIn('agent-flow-X.Y.Z-skill.zip',section)
            self.assertIn('~/.agents/skills/agent-flow/SKILL.md',section)
            self.assertNotIn('npx',section)
            self.assertIn('check-installed-package.py',section)
        for name in ('README.md','README.ru.md'):
            self.assertIn('releases/latest',(ROOT/name).read_text())
        print('release-docs-zip-path-reviewed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
