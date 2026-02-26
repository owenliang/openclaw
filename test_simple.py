import asyncio
import httpx
import json

async def test_simple_request():
    """测试单个简单请求"""
    print("🧪 开始测试单个请求...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        session_id = "test_simple_123"
        
        print(f"📤 发送请求到 session_id: {session_id}")
        
        try:
            async with client.stream(
                "POST", 
                "http://localhost:8000/chat",
                json={
                    "session_id": session_id,
                    "content": [{"type": "text", "text": "你好"}],
                    "deepresearch": False
                }
            ) as response:
                print(f"✅ 连接成功! Status: {response.status_code}")
                
                count = 0
                async for line in response.aiter_lines():
                    if line:
                        print(f"📨 Line {count}: {line}")
                        count += 1
                        if count > 10:  # 只读取前10行
                            print("⏹️  已读取足够响应,停止...")
                            break
                
                print(f"\n✅ 测试成功! 总共收到 {count} 行响应")
                
        except Exception as e:
            print(f"❌ 测试失败: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_simple_request())
