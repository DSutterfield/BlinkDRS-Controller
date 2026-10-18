import unittest
from unittest.mock import patch
import numpy as np
import subject_type_check as check
from test_subject_type_check import gallery, vector, MODEL


class PreparedReferenceTests(unittest.TestCase):
    def test_random_queries_produce_identical_rankings_and_support(self):
        rng = np.random.default_rng(17)
        refs = gallery()
        for row in refs:
            row['vector'] = (np.array(row['vector']) + rng.normal(0, .03, 384)).tolist()
        refs[0]['support_excluded'] = True
        prepared = check.prepare_references(refs)
        for query in [vector(i) for i in range(3)] + [rng.normal(size=384).tolist() for _ in range(8)]:
            self.assertEqual(check.rank_types(refs, query, MODEL),
                             check.rank_types(prepared, query, MODEL))

    def test_only_query_is_normalized_on_repeated_rank(self):
        refs = check.prepare_references(gallery())
        check.rank_types(refs, vector(0), MODEL)
        with patch.object(check, 'normalized', wraps=check.normalized) as normalize:
            check.rank_types(refs, vector(1), MODEL)
            self.assertEqual(normalize.call_count, 1)

    def test_snapshot_is_frozen_and_new_clip_sees_reference_edits(self):
        refs = gallery()
        prepared = check.prepare_references(refs)
        before = check.rank_types(prepared, vector(0), MODEL)
        for row in refs:
            row['quality_score'] = .1
            row['vector'][0] = .5
        self.assertEqual(check.rank_types(prepared, vector(0), MODEL), before)
        self.assertEqual(check.rank_types(check.prepare_references(refs), vector(0), MODEL), [])
        with self.assertRaises(TypeError):
            prepared[0]['subject_type'] = 'Dog'
        with self.assertRaises(TypeError):
            prepared[0]['vector'][0] = 0

    def test_invalid_and_insufficient_reference_gates_are_preserved(self):
        for refs in ([], gallery()[:3], [dict(r, vector=None) for r in gallery()],
                     [dict(r, model_version='other') for r in gallery()]):
            self.assertEqual(check.rank_types(refs, vector(0), MODEL),
                             check.rank_types(check.prepare_references(refs), vector(0), MODEL))
