from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SkillContractTests(unittest.TestCase):
    def test_frontmatter_version_and_small_entrypoint(self):
        text = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        match = re.match(r'^---\n(.*?)\n---\n', text, re.DOTALL)
        self.assertIsNotNone(match)
        self.assertIn('name: apm', match.group(1))
        self.assertIn('version: "1.0.0"', match.group(1))
        description = re.search(r'(?m)^description:\s*(.+)$', match.group(1)).group(1)
        self.assertLessEqual(len(description), 1024)
        self.assertLess(len(text.split()), 1600)
        self.assertLess(len(text.splitlines()), 160)

    def test_provider_names_stay_out_of_core_protocol(self):
        paths = [ROOT / 'SKILL.md', *sorted((ROOT / 'references').glob('*.md'))]
        combined = '\n'.join(p.read_text(encoding='utf-8') for p in paths)
        for name in ('gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna'):
            self.assertNotIn(name, combined)
        for tier in ('strong', 'balanced', 'economy'):
            self.assertIn(tier, combined)

    def test_ui_metadata_keeps_explicit_activation(self):
        text = (ROOT / 'agents/openai.yaml').read_text(encoding='utf-8')
        self.assertIn('allow_implicit_invocation: false', text)
        prompt = re.search(r'default_prompt: "([^"]+)"', text).group(1)
        self.assertTrue(prompt.startswith('$apm'))
        label = re.search(r'short_description: "([^"]+)"', text).group(1)
        self.assertTrue(25 <= len(label) <= 64)
        self.assertNotIn('dependencies:', text)

    def test_all_published_json_parses(self):
        paths = [*sorted((ROOT / 'assets').rglob('*.json')),
                 *sorted((ROOT / 'schemas').glob('*.json')), ROOT / 'evals/evals.json']
        for path in paths:
            with self.subTest(path=path):
                json.loads(path.read_text(encoding='utf-8'))

    def test_eval_scenarios_keep_existing_cases_and_add_relay_editor_and_budget(self):
        value = json.loads((ROOT / 'evals/evals.json').read_text(encoding='utf-8'))
        self.assertEqual(value['skill_name'], 'apm')
        ids = [item['id'] for item in value['evals']]
        self.assertEqual(ids[:26], list('ABCDEFGHIJKLMNOPQRSTUVWXYZ'))
        self.assertEqual(len(ids), len(set(ids)))
        for item in value['evals']:
            self.assertTrue(item['prompt'].strip())
            self.assertTrue(item['expected_output'].strip())
            self.assertGreaterEqual(len(item['assertions']), 2)

    def test_public_docs_share_version_interfaces_and_evidence_boundary(self):
        for name in ('README.md', 'README.zh-CN.md'):
            text = (ROOT / name).read_text(encoding='utf-8')
            for marker in ('v1.0.0', '$apm continue worker-1', '$apm continue lead', '$apm status',
                           'Benchmark pending', '10%', 'purpose_usage', 'TRANSPORT.json', 'VS Code',
                           'python scripts/statectl.py', 'Apache-2.0', 'reassign-worker', 'reopen-project',
                           'PROJECT_STATUS.md', 'memory.jsonl', 'standalone', 'delegation-policy.md'):
                self.assertIn(marker, text)

    def test_executable_statectl_examples_use_interpreter(self):
        # Prevent the original ambiguous inline executable form from returning.
        paths = [ROOT / 'SKILL.md', ROOT / 'README.md', ROOT / 'README.zh-CN.md',
                 *sorted((ROOT / 'references').glob('*.md')), *sorted((ROOT / 'examples').glob('*.md'))]
        for path in paths:
            text = path.read_text(encoding='utf-8')
            self.assertNotRegex(text, r'`statectl\.py\s+[a-z]')
            self.assertNotRegex(text, r'(?m)^\s*(?:scripts[/\\])?statectl\.py\s')

    def test_all_local_document_links_resolve(self):
        paths = [ROOT / 'SKILL.md', ROOT / 'README.md', ROOT / 'README.zh-CN.md',
                 *sorted((ROOT / 'references').glob('*.md')), *sorted((ROOT / 'examples').glob('*.md')),
                 *sorted((ROOT / 'profiles').glob('*.md')), *sorted((ROOT / 'benchmarks').glob('*.md'))]
        for source in paths:
            for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', source.read_text(encoding='utf-8')):
                if '://' in target or target.startswith('#'):
                    continue
                with self.subTest(source=source, target=target):
                    self.assertTrue((source.parent / target.split('#')[0]).resolve().exists())

    def test_explicit_gate_and_essential_workflow_are_discoverable(self):
        skill = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        for reference in ('delegation-and-roles.md', 'runtime-state.md', 'host-dispatch.md', 'delegation-policy.md'):
            self.assertIn(reference, skill)
        for invariant in ('never activates', 'allowed_scope', '--assignment-revision',
                          'Never validate or gate reasoning', 'reopen-project', 'PROJECT_STATUS.md'):
            self.assertIn(invariant, skill)


if __name__ == '__main__':
    unittest.main()
