"""tests.py — 核心模块单元测试"""
import sys, os, json, unittest
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

# ── molecule_types ──
class TestMoleculeTypes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from molecule_types import MOLECULE_TYPES, FUSION_WEIGHTS
        cls.MT = MOLECULE_TYPES
        cls.FW = FUSION_WEIGHTS

    def test_all_6_types_defined(self):
        for t in ['hook_clash','identity_twist','emotional_resonance','quote_rhythm','cinematic_beauty','suspense_hook']:
            self.assertIn(t, self.MT)
            self.assertIn('clip_dur', self.MT[t])
            self.assertIn('min_clips', self.MT[t])
            self.assertIn('max_clips', self.MT[t])

    def test_fusion_weights_all_types(self):
        for t in ['hook_clash','identity_twist','emotional_resonance','quote_rhythm','cinematic_beauty','suspense_hook']:
            self.assertIn(t, self.FW, f"FUSION_WEIGHTS missing {t}")

    def test_clip_dur_ranges(self):
        for t, m in self.MT.items():
            lo, hi = m['clip_dur']
            self.assertLess(lo, hi, f"{t}: clip_dur min >= max")
            self.assertGreater(lo, 0, f"{t}: clip_dur min <= 0")

    def test_fusion_weights_sum(self):
        for t, w in self.FW.items():
            s = sum(v for k, v in w.items() if k not in ('_note','_desc'))
            self.assertAlmostEqual(s, 1.0, delta=0.05, msg=f"{t}: weights sum={s:.2f}")

# ── edit_utils ──
class TestEditUtils(unittest.TestCase):
    def test_parse_vision_line_v2(self):
        from edit_utils import parse_vision_line
        v2_json = '{"shot":"特写","event":"打斗","emo":4,"action_level":3,"visual_quality":4,"face_quality":"正脸","action_direction":"增强","emotion_trend":"爆发"}'
        r = parse_vision_line(v2_json)
        self.assertEqual(r['_v2']['shot'], '特写')
        self.assertEqual(r['emotion'], 4)
        self.assertEqual(r['action_direction'], '增强')
        self.assertEqual(r['emotion_trend'], '爆发')

    def test_parse_vision_line_legacy(self):
        from edit_utils import parse_vision_line
        r = parse_vision_line("情绪：4。类型：恐怖/对峙。")
        self.assertEqual(r['emotion'], 4)
        self.assertIn('对峙', r['scene_types'])

    def test_audio_field_extraction(self):
        from edit_utils import parse_vision_line
        v2 = '{"shot":"中景","event":"日常","emo":2,"audio_energy":4,"speech_density":3,"transcript_excerpt":"测试台词"}'
        r = parse_vision_line(v2)
        self.assertEqual(r['audio_energy'], 4)
        self.assertEqual(r['speech_density'], 3)
        self.assertEqual(r['transcript_excerpt'], '测试台词')

    def test_v3_field_extraction(self):
        from edit_utils import parse_vision_line
        v3 = '{"shot":"近景","event":"对峙","promo_value":4,"hook_value":5,"cut_role":"hook","best_cut":"on_action","pre_roll":0.5,"suggested_duration":2.5}'
        r = parse_vision_line(v3)
        self.assertEqual(r['promo_value'], 4)
        self.assertEqual(r['hook_value'], 5)
        self.assertEqual(r['best_cut'], 'on_action')
        self.assertEqual(r['pre_roll'], 0.5)

# ── selection_constraints ──
class TestSelectionConstraints(unittest.TestCase):
    def test_is_legacy_empty(self):
        from selection_constraints import _is_legacy_data
        self.assertTrue(_is_legacy_data([]))

    def test_is_legacy_v2_rich(self):
        from selection_constraints import _is_legacy_data
        clips = [{'audio_energy': 4, 'speech_density': 3} for _ in range(10)]
        self.assertFalse(_is_legacy_data(clips))

    def test_is_legacy_v1_poor(self):
        from selection_constraints import _is_legacy_data
        clips = [{'audio_energy': 1, 'speech_density': 1} for _ in range(10)]
        self.assertTrue(_is_legacy_data(clips))

    def test_deduplicate_clips(self):
        from selection_constraints import deduplicate_clips
        clips = [
            {'ep': 1, 'time': 10}, {'ep': 1, 'time': 12},
            {'ep': 1, 'time': 30}, {'ep': 2, 'time': 10},
        ]
        r = deduplicate_clips(clips, window_seconds=5)
        self.assertEqual(len(r), 3)  # ep1:10+12→1, ep1:30→1, ep2:10→1

# ── config ──
class TestConfig(unittest.TestCase):
    def test_singleton(self):
        from config import get_project_config, get_engine_config
        p1 = get_project_config()
        p2 = get_project_config()
        self.assertIs(p1, p2)

    def test_project_fields(self):
        from config import get_project_config
        p = get_project_config()
        for k in ['project_name', 'media_dir', 'work_dir', 'analysis_v2', 'analysis_v3']:
            self.assertIn(k, p, f"project config missing {k}")

if __name__ == '__main__':
    unittest.main(verbosity=2)
