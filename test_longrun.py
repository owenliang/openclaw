import asyncio
import httpx
import uuid
import json
import random

BASE_URL = "http://localhost:8000"
TOTAL_ROUNDS = 100

# 多样化的问题，模拟真实对话压力
QUESTIONS = [
    "用一句话介绍Python",
    "1+1等于几",
    "今天天气怎么样",
    "帮我写一个冒泡排序",
    "什么是递归？举个例子",
    "解释一下什么是闭包",
    "HTTP和HTTPS的区别是什么",
    "什么是RESTful API",
    "用Python写一个斐波那契数列",
    "解释一下GIL是什么",
    "什么是异步编程",
    "Linux中如何查看进程",
    "什么是Docker",
    "解释一下TCP三次握手",
    "什么是哈希表",
    "二分查找的时间复杂度是多少",
    "什么是设计模式",
    "解释一下单例模式",
    "什么是依赖注入",
    "如何优化SQL查询",
]

async def chat_once(client: httpx.AsyncClient, session_id: str, message: str, round_num: int) -> bool:
    request_data = {
        "session_id": session_id,
        "content": [{"type": "text", "text": message}],
        "deepresearch": False,
    }
    try:
        async with client.stream("POST", f"{BASE_URL}/chat", json=request_data, timeout=120.0) as resp:
            got_content = False
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        if isinstance(data, dict) and data.get("last"):
                            got_content = True
                    except Exception:
                        pass
        if got_content:
            print(f"  ✅ Round {round_num:>3}/{TOTAL_ROUNDS}: OK  | Q: {message[:30]}")
            return True
        else:
            print(f"  ⚠️  Round {round_num:>3}/{TOTAL_ROUNDS}: 无last标记 | Q: {message[:30]}")
            return False
    except Exception as e:
        print(f"  ❌ Round {round_num:>3}/{TOTAL_ROUNDS}: {type(e).__name__}: {e} | Q: {message[:30]}")
        return False


async def test_longrun():
    session_id = str(uuid.uuid4())
    print(f"\n🧪 长跑测试: 同一Session连续 {TOTAL_ROUNDS} 轮对话")
    print(f"📝 Session ID: {session_id}")
    print("=" * 70)

    failed_rounds = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for i in range(1, TOTAL_ROUNDS + 1):
            question = random.choice(QUESTIONS)
            ok = await chat_once(client, session_id, question, i)
            if not ok:
                failed_rounds.append(i)

    print("\n" + "=" * 70)
    print("📊 测试结果:")
    print("=" * 70)
    passed = TOTAL_ROUNDS - len(failed_rounds)
    print(f"✅ 通过: {passed}/{TOTAL_ROUNDS}")
    if failed_rounds:
        print(f"❌ 失败轮次: {failed_rounds}")
        print("⚠️  测试未通过")
    else:
        print("🎉 所有轮次通过，服务稳定运行！")
    return len(failed_rounds) == 0


if __name__ == "__main__":
    print("🏃 长跑稳定性测试")
    print("=" * 70)
    print(f"确保服务器运行在 {BASE_URL}")
    print("=" * 70)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        passed = loop.run_until_complete(test_longrun())
        exit(0 if passed else 1)
    finally:
        loop.close()
