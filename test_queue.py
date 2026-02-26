import asyncio
import httpx
import uuid
import json
from typing import List

BASE_URL = "http://localhost:8000"

async def send_chat_request(client: httpx.AsyncClient, session_id: str, message: str, request_num: int):
    """发送聊天请求并收集响应"""
    print(f"  🚀 请求{request_num} 开始发送...")
    
    request_data = {
        "session_id": session_id,
        "content": [{"type": "text", "text": message}],
        "deepresearch": False
    }
    
    responses = []
    request_id = None
    
    try:
        async with client.stream("POST", f"{BASE_URL}/chat", json=request_data, timeout=300.0) as response:
            async for line in response.aiter_lines():
                if line:
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if isinstance(data, dict) and "request_id" in data:
                                request_id = data["request_id"]
                                print(f"  ✅ 请求{request_num} 获得 request_id: {request_id}")
                            responses.append(data)
                        except:
                            responses.append(line)
                    else:
                        responses.append(line)
        
        print(f"  ✅ 请求{request_num} 完成,收到 {len(responses)} 条响应")
        return {
            "request_num": request_num,
            "message": message,
            "responses": responses,
            "response_count": len(responses),
            "request_id": request_id,
            "success": True
        }
    except Exception as e:
        print(f"  ❌ 请求{request_num} 失败: {type(e).__name__}: {e}")
        return {
            "request_num": request_num,
            "message": message,
            "responses": [],
            "response_count": 0,
            "request_id": None,
            "success": False,
            "error": str(e)
        }

async def test_sequential_queue():
    """测试同一session的请求队列(顺序处理)"""
    session_id = str(uuid.uuid4())
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        print(f"\n🧪 测试1: 同一Session的请求队列处理")
        print(f"📝 Session ID: {session_id}")
        print("="*80)
        
        # 准备3个简单请求
        messages = [
            "你好",
            "1+1等于几",
            "谢谢"
        ]
        
        print("\n📤 顺序发送3个请求到同一个session...")
        
        # 顺序发送请求,确保1、2、3是先后到达服务器
        tasks = []
        for i, msg in enumerate(messages):
            task = asyncio.create_task(send_chat_request(client, session_id, msg, i+1))
            tasks.append(task)
            await asyncio.sleep(0.1)  # 确保顺序到达
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 统计结果
        print("\n" + "="*80)
        print("📊 测试结果:")
        print("="*80)
        
        success_count = sum(1 for r in results if not isinstance(r, Exception) and r.get('success'))
        print(f"\n✅ 成功: {success_count}/{len(messages)}")
        
        for result in results:
            if isinstance(result, Exception):
                print(f"❌ 请求异常: {result}")
            elif not result.get('success'):
                print(f"❌ 请求{result['request_num']} 失败: {result.get('error', '未知错误')}")
            else:
                print(f"✅ 请求{result['request_num']}: 响应数={result['response_count']}, request_id={result['request_id']}")

async def test_concurrent_sessions():
    """测试不同session的并发请求"""
    
    print(f"\n🧪 测试2: 不同Session的并发请求")
    print("="*80)
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        # 准备3个不同的session
        sessions = [
            {"session_id": str(uuid.uuid4()), "message": f"测试消息{i+1}"}
            for i in range(3)
        ]
        
        print(f"\n📤 并发发送3个请求到不同session...")
        
        tasks = [
            send_chat_request(client, s["session_id"], s["message"], i+1)
            for i, s in enumerate(sessions)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 统计结果
        print("\n" + "="*80)
        print("📊 测试结果:")
        print("="*80)
        
        success_count = sum(1 for r in results if not isinstance(r, Exception) and r.get('success'))
        print(f"\n✅ 成功: {success_count}/{len(sessions)}")
        
        for result in results:
            if isinstance(result, Exception):
                print(f"❌ 请求异常: {result}")
            elif not result.get('success'):
                print(f"❌ 请求{result['request_num']} 失败: {result.get('error', '未知错误')}")
            else:
                print(f"✅ 请求{result['request_num']}: 响应数={result['response_count']}, request_id={result['request_id']}")

async def send_long_request_with_callback(client: httpx.AsyncClient, session_id: str, message: str, request_num: int, callback=None):
    """发送一个需要较长时间处理的请求,并在获得request_id后回调"""
    print(f"  🚀 请求{request_num} 开始发送...")
    
    request_data = {
        "session_id": session_id,
        "content": [{"type": "text", "text": message}],
        "deepresearch": False
    }
    
    responses = []
    request_id = None
    
    try:
        async with client.stream("POST", f"{BASE_URL}/chat", json=request_data, timeout=300.0) as response:
            async for line in response.aiter_lines():
                if line:
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if isinstance(data, dict):
                                if "request_id" in data:
                                    request_id = data["request_id"]
                                    print(f"  ✅ 请求{request_num} 获得 request_id: {request_id}")
                                    # 回调通知
                                    if callback:
                                        await callback(request_id)
                                if data.get('cancel'):
                                    print(f"  🛑 请求{request_num} 被取消")
                                    responses.append(data)
                                    break
                            responses.append(data)
                        except:
                            responses.append(line)
                    else:
                        responses.append(line)
        
        # 判断是否被取消:要么有cancel标记,要么只有1条响应(只有request_id)
        is_cancelled = any(isinstance(r, dict) and r.get('cancel') for r in responses) or len(responses) == 1
        status_text = "🛑 已取消" if is_cancelled else "✅ 完成"
        print(f"  {status_text} 请求{request_num},收到 {len(responses)} 条响应")
        return {
            "request_num": request_num,
            "message": message,
            "responses": responses,
            "response_count": len(responses),
            "request_id": request_id,
            "success": True,
            "cancelled": is_cancelled
        }
    except Exception as e:
        print(f"  ❌ 请求{request_num} 失败: {type(e).__name__}: {e}")
        return {
            "request_num": request_num,
            "message": message,
            "responses": [],
            "response_count": 0,
            "request_id": request_id,
            "success": False,
            "cancelled": False,
            "error": str(e)
        }

async def cancel_request(client: httpx.AsyncClient, session_id: str, request_id: str):
    """取消指定请求"""
    try:
        response = await client.get(f"{BASE_URL}/stop", params={"session_id": session_id, "request_id": request_id})
        return response.json()
    except Exception as e:
        return {"error": str(e)}

async def test_cancel_request():
    """测试取消请求功能"""
    session_id = str(uuid.uuid4())
    
    print(f"\n🧪 测试3: 取消队列中的请求")
    print(f"📝 Session ID: {session_id}")
    print("="*80)
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        # 准备5个请求,使用中等复杂度的问题
        messages = [
            "请用150字介绍Python编程语言的特点和应用场景",
            "请用150字介绍JavaScript的发展历程和核心特性",
            "请用150字介绍Java语言在企业级应用中的优势",  # 这个请求会被取消
            "请用150字介绍Go语言的并发模型和应用领域",
            "请用150字介绍Rust语言的内存安全机制"
        ]
        
        print("\n📤 顺序发送5个请求到同一个session...")
        
        cancel_target_index = 2  # 取消第3个请求
        target_request_id = None
        
        # 当第3个请求获取到request_id后立即取消
        async def on_target_request_id(req_id):
            nonlocal target_request_id
            target_request_id = req_id
            print(f"\n{'='*80}")
            print(f"🛑 检测到请求{cancel_target_index + 1}的request_id: {target_request_id}")
            print(f"🛑 立即发起取消请求...")
            cancel_result = await cancel_request(client, session_id, target_request_id)
            print(f"   取消API返回: {cancel_result}")
            print(f"{'='*80}\n")
        
        # 顺序发送请求
        tasks = []
        for i, msg in enumerate(messages):
            # 如果是目标请求,传入回调函数
            callback = on_target_request_id if i == cancel_target_index else None
            task = asyncio.create_task(
                send_long_request_with_callback(client, session_id, msg, i + 1, callback)
            )
            tasks.append(task)
            await asyncio.sleep(0.1)  # 确保顺序发送
        
        # 等待所有请求完成
        print("\n⏳ 等待所有请求完成...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 统计结果
        print("\n" + "="*80)
        print("📊 测试结果:")
        print("="*80)
        
        success_count = sum(1 for r in results if not isinstance(r, Exception) and r.get('success'))
        # 判断取消:要么有cancel标记,要么只有1条响应(只有request_id)
        cancelled_count = sum(
            1 for r in results 
            if not isinstance(r, Exception) and (r.get('cancelled') or r.get('response_count') == 1)
        )
        
        print(f"\n✅ 成功完成: {success_count}/{len(messages)}")
        print(f"🛑 被取消: {cancelled_count}/{len(messages)}")
        
        for result in results:
            if isinstance(result, Exception):
                print(f"\n❌ 请求异常: {result}")
            elif not result.get('success'):
                print(f"\n❌ 请求{result['request_num']} 失败: {result.get('error', '未知错误')}")
            else:
                # 判断是否被取消:有cancel标记或只有1条响应
                is_cancelled = result.get('cancelled') or result.get('response_count') == 1
                status = "🛑 已取消" if is_cancelled else "✅ 正常完成"
                print(f"\n{status} 请求{result['request_num']}: 响应数={result['response_count']}, request_id={result['request_id']}")
                if result['request_num'] == cancel_target_index + 1:
                    print(f"   验证: {'✅ 取消成功' if is_cancelled else '❌ 取消失败'}")
                
        # 返回统计结果
        return {
            "success_count": success_count,
            "cancelled_count": cancelled_count,
            "total_count": len(messages)
        }

async def test_session_expiration():
    """测试session过期回收机制"""
    
    print(f"\n🧪 测试4: Session过期回收测试")
    print("="*80)
    print("每个session发送1次请求,等待65秒后再发起第2次,验证session过期后的正确性")
    print("="*80)
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        # 准备5个不同的session
        sessions = [
            {"session_id": str(uuid.uuid4()), "name": f"Session{i+1}"}
            for i in range(5)
        ]
        
        print(f"\n📤 并发测试 {len(sessions)} 个session的过期回收...")
        
        async def test_single_session_expiration(session_info, index):
            session_id = session_info["session_id"]
            name = session_info["name"]
            
            try:
                # 第1次请求
                print(f"\n  [{name}] 🚀 第1次请求开始...")
                result1 = await send_chat_request(client, session_id, f"{name}的第1次请求", index*10+1)
                print(f"  [{name}] ✅ 第1次请求完成: request_id={result1.get('request_id')}, 响应数={result1.get('response_count')}")
                
                # 等待65秒,让session过期(60秒超时)
                print(f"  [{name}] ⏳ 等待65秒,让session过期...")
                await asyncio.sleep(65)
                
                # 第2次请求(应该会创建新session)
                print(f"  [{name}] 🚀 第2次请求开始(过期后)...")
                result2 = await send_chat_request(client, session_id, f"{name}的第2次请求(过期后)", index*10+2)
                print(f"  [{name}] ✅ 第2次请求完成: request_id={result2.get('request_id')}, 响应数={result2.get('response_count')}")
                
                return {
                    "session_id": session_id,
                    "name": name,
                    "request1": result1,
                    "request2": result2,
                    "success": result1.get('success') and result2.get('success')
                }
                
            except Exception as e:
                print(f"  [{name}] ❌ 异常: {type(e).__name__}: {e}")
                return {
                    "session_id": session_id,
                    "name": name,
                    "request1": None,
                    "request2": None,
                    "success": False,
                    "error": str(e)
                }
        
        # 并发测试所有session
        tasks = [
            test_single_session_expiration(session, i)
            for i, session in enumerate(sessions)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 统计结果
        print("\n" + "="*80)
        print("📊 测试结果:")
        print("="*80)
        
        success_count = sum(1 for r in results if not isinstance(r, Exception) and r.get('success'))
        failed_count = len(results) - success_count
        
        print(f"\n✅ 成功: {success_count}/{len(sessions)}")
        print(f"❌ 失败: {failed_count}/{len(sessions)}")
        
        for result in results:
            if isinstance(result, Exception):
                print(f"\n❌ Session异常: {result}")
            elif result.get('success'):
                print(f"\n✅ {result['name']} (成功)")
                print(f"   Session ID: {result['session_id'][:16]}...")
                print(f"   第1次: request_id={result['request1']['request_id'][:16]}..., 响应={result['request1']['response_count']}条")
                print(f"   第2次: request_id={result['request2']['request_id'][:16]}..., 响应={result['request2']['response_count']}条")
            else:
                print(f"\n❌ {result['name']} (失败)")
                if 'error' in result:
                    print(f"   错误: {result['error']}")
        
        # 返回统计结果
        return {
            "success_count": success_count,
            "failed_count": failed_count,
            "total_count": len(sessions)
        }

if __name__ == "__main__":
    print("🧪 Session 请求队列、并发与取消测试")
    print("="*80)
    print("确保服务器运行在 http://localhost:8000")
    print("="*80)
    
    # 运行2轮测试并统计结果
    test_rounds = 2
    results_summary = {
        "test1": {"success": 0, "failed": 0, "total_requests": 0},
        "test2": {"success": 0, "failed": 0, "total_requests": 0},
        "test3": {"success": 0, "failed": 0, "cancelled": 0, "total_requests": 0},
        "test4": {"success": 0, "failed": 0, "total_requests": 0}
    }
    
    print(f"\n🔄 开始执行 {test_rounds} 轮测试...\n")
    
    for round_num in range(1, test_rounds + 1):
        print(f"\n{'#'*80}")
        print(f"# 第 {round_num}/{test_rounds} 轮测试")
        print(f"{'#'*80}")
        
        # 测试1: 同一session的请求队列
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(test_sequential_queue())
            # 简单统计:如果没有异常就认为成功
            results_summary["test1"]["success"] += 1
            results_summary["test1"]["total_requests"] += 3
        except Exception as e:
            print(f"\n❌ 测试1 异常: {e}")
            results_summary["test1"]["failed"] += 1
        finally:
            loop.close()
        
        # 测试2: 不同session的并发
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(test_concurrent_sessions())
            results_summary["test2"]["success"] += 1
            results_summary["test2"]["total_requests"] += 3
        except Exception as e:
            print(f"\n❌ 测试2 异常: {e}")
            results_summary["test2"]["failed"] += 1
        finally:
            loop.close()
        
        # 测试3: 取消请求
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            # 这里需要返回结果来统计取消情况
            test3_result = loop.run_until_complete(test_cancel_request())
            results_summary["test3"]["success"] += 1
            results_summary["test3"]["total_requests"] += 5
            if test3_result and test3_result.get("cancelled_count", 0) > 0:
                results_summary["test3"]["cancelled"] += test3_result["cancelled_count"]
        except Exception as e:
            print(f"\n❌ 测试3 异常: {e}")
            results_summary["test3"]["failed"] += 1
        finally:
            loop.close()
        
        # 测试4: session过期回收
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            test4_result = loop.run_until_complete(test_session_expiration())
            results_summary["test4"]["success"] += 1
            results_summary["test4"]["total_requests"] += 10  # 5个session * 2次请求
        except Exception as e:
            print(f"\n❌ 测试4 异常: {e}")
            results_summary["test4"]["failed"] += 1
        finally:
            loop.close()
        
        # 每轮之间稍微等待
        if round_num < test_rounds:
            import time
            time.sleep(1)
    
    # 打印统计结果
    print(f"\n\n{'='*80}")
    print(f"📊 测试统计结果 ({test_rounds}轮)")
    print(f"{'='*80}\n")
    
    # 表头
    print(f"{'|测试项目':<20} | {'|成功次数':<12} | {'|失败次数':<12} | {'|总请求数':<12} | {'|取消数':<12} | {'|成功率':<12} |")
    print(f"{'-'*20}|{'-'*14}|{'-'*14}|{'-'*14}|{'-'*14}|{'-'*14}|")
    
    # 测试1
    test1 = results_summary["test1"]
    success_rate1 = f"{test1['success']/test_rounds*100:.1f}%" if test_rounds > 0 else "N/A"
    print(f"|测试1:队列处理      | {test1['success']:^12} | {test1['failed']:^12} | {test1['total_requests']:^12} | {'N/A':^12} | {success_rate1:^12} |")
    
    # 测试2
    test2 = results_summary["test2"]
    success_rate2 = f"{test2['success']/test_rounds*100:.1f}%" if test_rounds > 0 else "N/A"
    print(f"|测试2:并发请求      | {test2['success']:^12} | {test2['failed']:^12} | {test2['total_requests']:^12} | {'N/A':^12} | {success_rate2:^12} |")
    
    # 测试3
    test3 = results_summary["test3"]
    success_rate3 = f"{test3['success']/test_rounds*100:.1f}%" if test_rounds > 0 else "N/A"
    print(f"|测试3:取消功能      | {test3['success']:^12} | {test3['failed']:^12} | {test3['total_requests']:^12} | {test3['cancelled']:^12} | {success_rate3:^12} |")
    
    # 测试4
    test4 = results_summary["test4"]
    success_rate4 = f"{test4['success']/test_rounds*100:.1f}%" if test_rounds > 0 else "N/A"
    print(f"|测试4:过期回收      | {test4['success']:^12} | {test4['failed']:^12} | {test4['total_requests']:^12} | {'N/A':^12} | {success_rate4:^12} |")
    
    print(f"{'-'*20}|{'-'*14}|{'-'*14}|{'-'*14}|{'-'*14}|{'-'*14}|")
    
    # 总计
    total_success = test1['success'] + test2['success'] + test3['success'] + test4['success']
    total_failed = test1['failed'] + test2['failed'] + test3['failed'] + test4['failed']
    total_tests = test_rounds * 4
    overall_rate = f"{total_success/total_tests*100:.1f}%" if total_tests > 0 else "N/A"
    print(f"|总计               | {total_success:^12} | {total_failed:^12} | {test1['total_requests']+test2['total_requests']+test3['total_requests']+test4['total_requests']:^12} | {test3['cancelled']:^12} | {overall_rate:^12} |")
    
    print(f"\n{'='*80}")
    
    # 结论
    if total_failed == 0:
        print("✅ 所有测试均通过!")
    else:
        print(f"⚠️  有 {total_failed} 个测试失败")
