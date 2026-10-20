from workbench.sam3_litetext_video_worker import _prompt_variants


def test_gripper_uses_robotics_synonym_only_after_original_prompt():
    assert _prompt_variants("gripper") == ("gripper", "end effector")
    assert _prompt_variants("robot gripper") == ("robot gripper", "end effector")


def test_prompt_variants_preserve_custom_prompts():
    assert _prompt_variants("  red cup  ") == ("  red cup  ",)
