#!/usr/bin/env python3
"""
测试调试日志功能的脚本

此脚本发送一个测试请求到反代服务，以验证是否能正确记录：
1. 目标API的原始响应内容
2. 正则处理后的响应内容
"""

import asyncio
import json
import aiohttp

async def test_debug_logs():
    """测试调试日志功能"""
    
    # 测试用的OpenAI API请求
    test_request = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {
                "role": "user", 
                "content": "请简单介绍一下人工智能。"
            }
        ],
        "stream": False  # 先测试非流式
    }
    
    # 假设服务器运行在 localhost:8000
    # 这里使用一个测试用的目标URL（实际测试时需要替换为有效的OpenAI兼容API）
    proxy_url = "http://localhost:8000/https://api.openai.com/v1/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-test-key-here"  # 需要有效的API密钥
    }
    
    print("📋 测试调试日志功能...")
    print(f"🎯 目标: {proxy_url}")
    print(f"📨 请求体: {json.dumps(test_request, ensure_ascii=False, indent=2)}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                proxy_url,
                json=test_request,
                headers=headers
            ) as response:
                print(f"📊 响应状态码: {response.status}")
                
                if response.status == 200:
                    response_data = await response.json()
                    print("✅ 请求成功！")
                    print(f"📄 响应内容: {json.dumps(response_data, ensure_ascii=False, indent=2)}")
                    print("\n🔍 请检查服务器日志中的调试信息：")
                    print("   - '请求体' 日志（换行JSON格式）")
                    print("   - '目标API原始响应内容' 日志")
                    print("   - '正则处理后的响应内容' 日志")
                else:
                    error_text = await response.text()
                    print(f"❌ 请求失败: {error_text}")
                    
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        print("💡 提示：请确保反代服务正在运行在 localhost:8000")

async def test_debug_logs_streaming():
    """测试流式请求的调试日志功能"""
    
    # 测试用的流式请求
    test_request = {
        "model": "gpt-3.5-turbo", 
        "messages": [
            {
                "role": "user",
                "content": "请简单介绍一下机器学习。"
            }
        ],
        "stream": True  # 流式请求
    }
    
    proxy_url = "http://localhost:8000/https://api.openai.com/v1/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-test-key-here"  # 需要有效的API密钥
    }
    
    print("\n📋 测试流式请求调试日志功能...")
    print(f"🎯 目标: {proxy_url}")
    print(f"📨 流式请求体: {json.dumps(test_request, ensure_ascii=False, indent=2)}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                proxy_url,
                json=test_request,
                headers=headers
            ) as response:
                print(f"📊 响应状态码: {response.status}")
                
                if response.status == 200:
                    print("✅ 流式请求成功，开始接收数据...")
                    
                    chunk_count = 0
                    async for chunk in response.content.iter_chunked(1024):
                        if chunk:
                            chunk_count += 1
                            print(f"📦 接收到数据块 {chunk_count}: {len(chunk)} 字节")
                            
                            # 只显示前几个数据块的内容
                            if chunk_count <= 3:
                                try:
                                    chunk_text = chunk.decode('utf-8')
                                    print(f"   内容预览: {chunk_text[:100]}...")
                                except:
                                    print(f"   二进制内容")
                    
                    print(f"✅ 流式请求完成，共接收 {chunk_count} 个数据块")
                    print("\n🔍 请检查服务器日志中的流式调试信息：")
                    print("   - '流式请求体' 日志（换行JSON格式）")
                    print("   - '流式响应完整原始内容' 日志")
                    print("   - '流式响应正则处理后内容' 日志")
                    print("   - '模拟流式响应正则处理后内容' 日志（如果启用了fake streaming）")
                else:
                    error_text = await response.text()
                    print(f"❌ 流式请求失败: {error_text}")
                    
    except Exception as e:
        print(f"❌ 流式连接失败: {e}")

if __name__ == "__main__":
    print("🧪 开始测试调试日志功能...")
    print("=" * 60)
    
    # 运行非流式测试
    asyncio.run(test_debug_logs())
    
    # 等待一下再运行流式测试
    print("\n" + "=" * 60)
    asyncio.run(test_debug_logs_streaming())
    
    print("\n" + "=" * 60)
    print("🏁 测试完成！")
    print("💡 提示：")
    print("   1. 确保在 config/settings.yaml 中设置 log_level: 'DEBUG'")
    print("   2. 检查服务器控制台输出中的调试日志")
    print("   3. 调试日志会显示完整的响应内容和正则处理结果") 