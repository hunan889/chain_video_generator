#!/usr/bin/env python3
"""
实际运行视频生成对比测试

生成两组视频用于对比：
1. Story 模式（Merged）- 2段连续视频
2. 标准 I2V - 2段独立视频

输出：
- 视频文件保存在 storage/videos/
- 性能数据保存在 comparison_results.json
- 对比报告保存在 comparison_report.txt
"""

import asyncio
import time
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from api.main import task_manager
from api.models.enums import ModelType, GenerateMode
from api.services.comfyui_client import ComfyUIClient


async def upload_test_image():
    """上传测试图片到 ComfyUI"""
    print("\n准备测试图片...")

    # 检查是否有测试图片
    test_images = list(Path("/home/gime/soft/wan22-service/storage/uploads").glob("*.jpg"))
    test_images.extend(list(Path("/home/gime/soft/wan22-service/storage/uploads").glob("*.png")))

    if not test_images:
        print("❌ 未找到测试图片，请先上传一张图片到 storage/uploads/")
        return None

    test_image = test_images[0]
    print(f"✓ 使用测试图片: {test_image.name}")

    # 上传到 ComfyUI
    client = task_manager.clients.get("a14b")
    if not client:
        print("❌ ComfyUI 客户端未就绪")
        return None

    with open(test_image, "rb") as f:
        image_data = f.read()

    result = await client.upload_image(image_data, test_image.name)
    uploaded_filename = result.get("name", test_image.name)
    print(f"✓ 图片已上传到 ComfyUI: {uploaded_filename}")

    return uploaded_filename


async def test_story_mode(image_filename: str):
    """测试 Story 模式"""
    print("\n" + "="*70)
    print("测试 1: Story 模式（Merged）")
    print("="*70)

    segments = [
        {
            "prompt": "A woman walking in a garden, natural lighting, cinematic",
            "model": "a14b",
            "width": 832,
            "height": 480,
            "num_frames": 49,
            "fps": 16,
            "steps": 10,
            "cfg": 1.0,
            "shift": 8.0,
            "motion_amplitude": 1.15,
            "motion_frames": 5,
            "boundary": 0.9,
            "image_filename": image_filename,
        },
        {
            "prompt": "She stops and smiles at the camera, warm atmosphere",
            "model": "a14b",
            "width": 832,
            "height": 480,
            "num_frames": 49,
            "fps": 16,
            "steps": 10,
            "cfg": 1.0,
            "shift": 8.0,
            "motion_amplitude": 1.15,
            "motion_frames": 5,
            "boundary": 0.9,
        }
    ]

    # 创建 chain
    chain_id = await task_manager.create_chain(
        segment_count=2,
        params={
            "segment_prompts": [s["prompt"] for s in segments],
            "story_mode": True,
        }
    )

    print(f"Chain ID: {chain_id}")
    print("开始生成...")

    start_time = time.time()
    await task_manager.run_chain(chain_id, segments)

    # 等待完成
    while True:
        chain = await task_manager.get_chain(chain_id)
        if not chain:
            return None, "Chain not found", None

        status = chain.get("status")
        progress = f"{chain.get('completed_segments', 0)}/{chain.get('total_segments', 0)}"

        print(f"\r进度: {progress} - 状态: {status}", end="", flush=True)

        if status == "completed":
            elapsed = time.time() - start_time
            print(f"\n✓ 完成！耗时: {elapsed/60:.2f} 分钟 ({elapsed:.1f} 秒)")
            video_url = chain.get("final_video_url", "")
            print(f"  视频: {video_url}")
            return chain, elapsed, video_url
        elif status in ("failed", "partial"):
            error = chain.get("error", "Unknown error")
            print(f"\n✗ 失败: {error}")
            return chain, f"Failed: {error}", None

        await asyncio.sleep(5)


async def test_standard_i2v(image_filename: str):
    """测试标准 I2V（2段独立）"""
    print("\n" + "="*70)
    print("测试 2: 标准 I2V（独立生成）")
    print("="*70)

    from api.services.workflow_builder import build_workflow

    model = ModelType.A14B
    results = []

    prompts = [
        "A woman walking in a garden, natural lighting, cinematic",
        "She stops and smiles at the camera, warm atmosphere"
    ]

    start_time = time.time()

    for i, prompt in enumerate(prompts, 1):
        print(f"\n生成第 {i} 段...")

        workflow = build_workflow(
            mode=GenerateMode.I2V,
            model=model,
            prompt=prompt,
            width=832,
            height=480,
            num_frames=49,
            fps=16,
            steps=10,
            cfg=1.0,
            shift=8.0,
            image_filename=image_filename,
            noise_aug_strength=0.05,
        )

        task_id = await task_manager.create_task(GenerateMode.I2V, model, workflow)
        print(f"Task ID: {task_id}")

        # 等待完成
        seg_start = time.time()
        while True:
            task = await task_manager.get_task(task_id)
            if not task:
                print(f"\n✗ 任务未找到")
                return None, "Task not found", []

            status = task.get("status")
            progress = task.get("progress", 0)

            print(f"\r进度: {progress*100:.1f}% - 状态: {status}", end="", flush=True)

            if status == "completed":
                seg_elapsed = time.time() - seg_start
                video_url = task.get("video_url", "")
                print(f"\n✓ 第 {i} 段完成！耗时: {seg_elapsed/60:.2f} 分钟 ({seg_elapsed:.1f} 秒)")
                print(f"  视频: {video_url}")
                results.append({
                    "segment": i,
                    "task_id": task_id,
                    "elapsed": seg_elapsed,
                    "video_url": video_url,
                })
                break
            elif status == "failed":
                error = task.get("error", "Unknown error")
                print(f"\n✗ 第 {i} 段失败: {error}")
                return None, f"Segment {i} failed: {error}", []

            await asyncio.sleep(3)

    total_elapsed = time.time() - start_time
    print(f"\n✓ 全部完成！总耗时: {total_elapsed/60:.2f} 分钟 ({total_elapsed:.1f} 秒)")

    return results, total_elapsed, [r["video_url"] for r in results]


async def main():
    """主测试流程"""
    print("="*70)
    print("视频生成性能对比测试 - 实际运行")
    print("="*70)
    print("\n测试配置:")
    print("  分辨率: 832x480")
    print("  FPS: 16")
    print("  Steps: 10")
    print("  段数: 2段（每段 49帧 ≈ 3秒）")
    print("  模型: A14B")

    # 启动 task_manager
    print("\n启动 TaskManager...")
    await task_manager.start()

    try:
        # 上传测试图片
        image_filename = await upload_test_image()
        if not image_filename:
            print("\n❌ 测试终止：无法准备测试图片")
            return

        results = {}

        # 测试 1: Story 模式
        story_result, story_time, story_video = await test_story_mode(image_filename)
        results["story_mode"] = {
            "elapsed_seconds": story_time if isinstance(story_time, (int, float)) else None,
            "video_url": story_video,
            "status": "completed" if isinstance(story_time, (int, float)) else "failed",
        }

        # 等待 GPU 冷却
        print("\n等待 30 秒让 GPU 冷却...")
        await asyncio.sleep(30)

        # 测试 2: 标准 I2V
        i2v_result, i2v_time, i2v_videos = await test_standard_i2v(image_filename)
        results["standard_i2v"] = {
            "elapsed_seconds": i2v_time if isinstance(i2v_time, (int, float)) else None,
            "video_urls": i2v_videos,
            "status": "completed" if isinstance(i2v_time, (int, float)) else "failed",
        }

        # 生成对比报告
        print("\n" + "="*70)
        print("性能对比结果")
        print("="*70)

        report_lines = []
        report_lines.append("="*70)
        report_lines.append("视频生成性能对比测试结果")
        report_lines.append("="*70)
        report_lines.append(f"\n测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append(f"测试配置: 832x480, 16fps, 10 steps, 2段")
        report_lines.append("")

        if isinstance(story_time, (int, float)) and isinstance(i2v_time, (int, float)):
            speedup = i2v_time / story_time

            report_lines.append(f"1. Story 模式（Merged）:")
            report_lines.append(f"   耗时: {story_time/60:.2f} 分钟 ({story_time:.1f} 秒)")
            report_lines.append(f"   视频: {story_video}")
            report_lines.append("")

            report_lines.append(f"2. 标准 I2V（独立生成）:")
            report_lines.append(f"   耗时: {i2v_time/60:.2f} 分钟 ({i2v_time:.1f} 秒)")
            for i, url in enumerate(i2v_videos, 1):
                report_lines.append(f"   视频 {i}: {url}")
            report_lines.append("")

            report_lines.append(f"性能对比:")
            report_lines.append(f"  Story 模式快 {speedup:.2f}x")
            report_lines.append(f"  节省时间: {(i2v_time - story_time)/60:.2f} 分钟")

            print(f"\n1. Story 模式: {story_time/60:.2f} 分钟")
            print(f"2. 标准 I2V:   {i2v_time/60:.2f} 分钟")
            print(f"\n⭐ Story 模式快 {speedup:.2f}x，节省 {(i2v_time - story_time)/60:.2f} 分钟")
        else:
            report_lines.append("测试未完全成功，请查看详细日志")
            print("\n⚠ 测试未完全成功")

        # 保存结果
        results_file = Path(__file__).parent / "comparison_results.json"
        with open(results_file, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n✓ 结果已保存: {results_file}")

        report_file = Path(__file__).parent / "comparison_report.txt"
        with open(report_file, "w") as f:
            f.write("\n".join(report_lines))
        print(f"✓ 报告已保存: {report_file}")

        print("\n视频文件位置:")
        print(f"  Story 模式: {story_video}")
        for i, url in enumerate(i2v_videos, 1):
            print(f"  标准 I2V 第{i}段: {url}")

    finally:
        await task_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
