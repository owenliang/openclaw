import asyncio
import httpx
import uuid
import json
import random

BASE_URL = "http://localhost:8000"
TOTAL_ROUNDS = 100000

# 多样化的问题，模拟真实对话压力
# 包含需要 web search 的实时信息类问题，以产生更长的工具调用交互
QUESTIONS = [
    # --- 需要 web search 的实时/新闻类问题（触发工具调用，产生更长内容）---
    "请用 web search 搜一下今天A股市场整体表现如何，有哪些板块涨幅较大，给我一个简要总结。",
    "请用 web search 搜一下最近一周比特币价格走势，现在大约多少钱一枚。",
    "请用 web search 搜一下今天最重要的科技新闻有哪些，列出3条。",
    "请用 web search 搜一下英伟达（NVIDIA）最新股价是多少，近期有什么重要动态。",
    "请用 web search 搜一下 OpenAI 最近发布了什么新产品或新功能。",
    "请用 web search 搜一下今天美元兑人民币汇率是多少。",
    "请用 web search 搜一下最近有什么重要的AI领域研究进展，给我列举2-3个。",
    "请用 web search 搜一下苹果公司最近有什么新闻，以及股价表现如何。",
    "请用 web search 搜一下最近全球经济形势，有没有重要的宏观数据发布。",
    "请用 web search 搜一下 Python 最新版本是什么，有哪些新特性。",
    "请用 web search 搜一下最近有哪些热门开源项目在 GitHub 上获得大量关注。",
    "请用 web search 搜一下今天国际油价大概是多少。",
    "请用 web search 搜一下特斯拉最新股价以及近期新闻。",
    "请用 web search 搜一下最近有什么重要的网络安全漏洞或事件披露。",
    "请用 web search 搜一下谷歌最新的AI产品或模型有哪些进展。",
    # --- 纯知识类问题（不需要搜索，快速响应）---
    "用一句话介绍Python",
    "帮我写一个冒泡排序",
    "什么是递归？举个例子",
    "解释一下什么是闭包",
    "HTTP和HTTPS的区别是什么",
    "用Python写一个斐波那契数列",
    "解释一下GIL是什么",
    "什么是Docker",
    "解释一下TCP三次握手",
    "什么是哈希表",
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
    session_id = 'c3965ee5-2032-482f-8850-643c15d58a97' #str(uuid.uuid4())
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
