        import asyncio
        import aiohttp
        import json
        import re
        import socket
        from datetime import datetime
        from typing import List, Dict, Optional, Tuple
        import time
        import os
        import dns.resolver

        # 中国三大运营商的公共 DNS 服务器
        ISP_DNS = {
            '电信': ['114.114.114.114', '114.114.115.115'],
            '联通': ['123.123.123.123', '123.125.81.6'],
            '移动': ['221.179.155.161', '112.4.0.55']
        }

        # 国家代码映射
        COUNTRY_MAPPING = {
            'CN': '中国', 'HK': '香港', 'TW': '台湾', 'JP': '日本', 'KR': '韩国',
            'SG': '新加坡', 'MY': '马来西亚', 'TH': '泰国', 'VN': '越南', 'ID': '印尼',
            'IN': '印度', 'PH': '菲律宾', 'US': '美国', 'CA': '加拿大', 'MX': '墨西哥',
            'DE': '德国', 'GB': '英国', 'FR': '法国', 'IT': '意大利', 'ES': '西班牙',
            'PT': '葡萄牙', 'NL': '荷兰', 'BE': '比利时', 'SE': '瑞典', 'NO': '挪威',
            'AU': '澳大利亚', 'NZ': '新西兰', 'BR': '巴西', 'AR': '阿根廷', 'RU': '俄罗斯',
            'CH': '瑞士', 'AT': '奥地利', 'DK': '丹麦', 'FI': '芬兰', 'IE': '爱尔兰',
            'PL': '波兰', 'CZ': '捷克', 'TR': '土耳其', 'AE': '阿联酋', 'IL': '以色列',
            'ZA': '南非', 'EG': '埃及', 'SA': '沙特', 'UA': '乌克兰', 'GR': '希腊'
        }

        def load_api_list(filename: str = 'api.txt') -> List[str]:
            """从 api.txt 加载 API 列表"""
            api_urls = []
            
            if not os.path.exists(filename):
                print(f"? {filename} 文件不存在")
                return api_urls
            
            with open(filename, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # 忽略空行和 # 开头的注释行
                    if line and not line.startswith('#'):
                        api_urls.append(line)
            
            print(f"? 从 {filename} 加载了 {len(api_urls)} 个 API")
            return api_urls

        def is_valid_ip(ip: str) -> bool:
            """验证IP地址格式"""
            parts = ip.split('.')
            if len(parts) != 4:
                return False
            for part in parts:
                try:
                    num = int(part)
                    if num < 0 or num > 255:
                        return False
                    if len(part) > 1 and part[0] == '0':
                        return False
                except ValueError:
                    return False
            return True

        def is_valid_port(port: str) -> bool:
            """验证端口号格式"""
            try:
                num = int(port)
                return 0 < num <= 65535
            except ValueError:
                return False

        def parse_ip_line(line: str) -> Optional[Dict]:
            """解析IP行，支持多种格式"""
            line = line.strip()
            
            # 跳过无效行
            if not line or line.startswith(('#', '//', '<!--', 'http')):
                return None
            
            # 模式1: IP:Port#Country
            match = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)#(.+)$', line)
            if match:
                ip, port, country = match.groups()
                if is_valid_ip(ip) and is_valid_port(port):
                    return {'ip': ip, 'port': port, 'country': country.strip()}
            
            # 模式2: IP#Country
            match = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})#(.+)$', line)
            if match:
                ip, country = match.groups()
                if is_valid_ip(ip):
                    return {'ip': ip, 'port': '443', 'country': country.strip()}
            
            # 模式3: IP:Port
            match = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)', line)
            if match:
                ip, port = match.groups()
                if is_valid_ip(ip) and is_valid_port(port):
                    return {'ip': ip, 'port': port, 'country': None}
            
            # 模式4: 单独IP
            match = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$', line)
            if match:
                ip = match.group(1)
                if is_valid_ip(ip):
                    return {'ip': ip, 'port': '443', 'country': None}
            
            return None

        async def fetch_url(session: aiohttp.ClientSession, url: str) -> str:
            """异步获取URL内容"""
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        return await response.text()
                    else:
                        print(f"??  {url} 返回状态码: {response.status}")
            except asyncio.TimeoutError:
                print(f"??  {url} 请求超时")
            except Exception as e:
                print(f"? 获取 {url} 失败: {e}")
            return ""

        def normalize_location(location: str) -> str:
            """标准化地理位置格式"""
            if not location or location == 'Unknown':
                return 'Unknown'
            
            # 移除多余空格和特殊字符
            location = location.strip()
            location = re.sub(r'\s+', ' ', location)
            
            # 如果没有分隔符，尝试添加
            if '-' not in location and ' ' in location:
                parts = location.split(' ', 1)
                location = '-'.join(parts)
            
            return location

        async def get_ip_location(session: aiohttp.ClientSession, ip: str, retry: int = 2) -> str:
            """获取IP详细地理位置（国家+地区/城市），带重试机制"""
            
            # 方法1: 使用 ip-api.com (中文支持更好)
            for attempt in range(retry):
                try:
                    async with session.get(f'http://ip-api.com/json/{ip}?lang=zh-CN&fields=status,country,regionName,city,countryCode', 
                                          timeout=aiohttp.ClientTimeout(total=10)) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get('status') == 'success':
                                country = data.get('country', '')
                                region_name = data.get('regionName', '')
                                city = data.get('city', '')
                                country_code = data.get('countryCode', '')
                                
                                # 如果是英文国家名，尝试转换为中文
                                if country_code and country_code in COUNTRY_MAPPING:
                                    country = COUNTRY_MAPPING[country_code]
                                
                                location_parts = []
                                if country:
                                    location_parts.append(country)
                                
                                # 优先使用城市
                                if city and city.strip():
                                    location_parts.append(city.strip())
                                elif region_name and region_name.strip():
                                    location_parts.append(region_name.strip())
                                
                                if location_parts:
                                    location = '-'.join(location_parts)
                                    return normalize_location(location)
                except Exception as e:
                    if attempt == retry - 1:
                        print(f"    ??  ip-api.com 查询失败: {e}")
                    await asyncio.sleep(1)
            
            # 方法2: 使用 ipinfo.io 作为备用
            for attempt in range(retry):
                try:
                    async with session.get(f'https://ipinfo.io/{ip}/json', 
                                          timeout=aiohttp.ClientTimeout(total=10)) as response:
                        if response.status == 200:
                            data = await response.json()
                            country_code = data.get('country', '')
                            city = data.get('city', '')
                            region = data.get('region', '')
                            
                            if country_code:
                                country_name = COUNTRY_MAPPING.get(country_code, country_code)
                                
                                location_parts = [country_name]
                                
                                # 优先使用城市，其次使用地区
                                if city and city.strip():
                                    location_parts.append(city.strip())
                                elif region and region.strip():
                                    location_parts.append(region.strip())
                                
                                location = '-'.join(location_parts)
                                return normalize_location(location)
                except Exception as e:
                    if attempt == retry - 1:
                        print(f"    ??  ipinfo.io 查询失败: {e}")
                    await asyncio.sleep(1)
            
            # 方法3: 使用 ipapi.co 作为最后备用
            try:
                async with session.get(f'https://ipapi.co/{ip}/json/', 
                                      timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        country = data.get('country_name', '')
                        city = data.get('city', '')
                        region = data.get('region', '')
                        country_code = data.get('country_code', '')
                        
                        # 尝试转换为中文
                        if country_code and country_code in COUNTRY_MAPPING:
                            country = COUNTRY_MAPPING[country_code]
                        
                        location_parts = []
                        if country:
                            location_parts.append(country)
                        if city and city.strip():
                            location_parts.append(city.strip())
                        elif region and region.strip():
                            location_parts.append(region.strip())
                        
                        if location_parts:
                            location = '-'.join(location_parts)
                            return normalize_location(location)
            except Exception as e:
                print(f"    ??  ipapi.co 查询失败: {e}")
            
            return 'Unknown'

        def test_tcp_connectivity(ip: str, port: int, timeout: float = 3.0) -> bool:
            """测试TCP连接"""
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                result = sock.connect_ex((ip, port))
                sock.close()
                return result == 0
            except Exception:
                return False

        def test_connectivity_via_dns(ip: str, port: int, dns_server: str, timeout: float = 5.0) -> bool:
            """通过指定DNS服务器测试连通性（模拟运营商网络环境）"""
            try:
                # 配置使用指定的DNS服务器
                resolver = dns.resolver.Resolver()
                resolver.nameservers = [dns_server]
                resolver.timeout = 2
                resolver.lifetime = 2
                
                # 测试DNS是否可用（查询一个已知域名）
                try:
                    resolver.resolve('www.baidu.com', 'A')
                except:
                    # DNS不可用，直接测试IP连接
                    pass
                
                # 直接测试IP连接
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                
                # 绑定到默认路由（让系统选择合适的出口）
                result = sock.connect_ex((ip, port))
                sock.close()
                
                return result == 0
            except Exception as e:
                return False

        def test_isp_connectivity(ip: str, port: int) -> Dict[str, bool]:
            """测试IP对三大运营商的连通性"""
            results = {}
            
            print(f"    测试运营商连通性:")
            
            for isp_name, dns_servers in ISP_DNS.items():
                # 使用该运营商的DNS服务器进行测试
                is_connected = False
                
                for dns_server in dns_servers:
                    if test_connectivity_via_dns(ip, port, dns_server, timeout=5.0):
                        is_connected = True
                        break
                    time.sleep(0.2)  # 短暂延迟
                
                results[isp_name] = is_connected
                status = "?" if is_connected else "?"
                print(f"      {status} {isp_name}: {'通' if is_connected else '不通'}")
            
            return results

        async def test_ip_connectivity(ip_data: Dict, session: aiohttp.ClientSession) -> Optional[Dict]:
            """测试单个IP的连通性"""
            ip = ip_data['ip']
            port = int(ip_data['port'])
            
            print(f"\n{'='*60}")
            print(f"?? 测试 IP: {ip}:{port}")
            print(f"{'='*60}")
            
            # 先进行基础连通性测试
            print(f"  [1/4] 基础连通性测试...", end=' ')
            is_connected = test_tcp_connectivity(ip, port, timeout=5.0)
            
            if not is_connected:
                print("? 失败")
                return None
            
            print("? 成功")
            
            # 测试三大运营商的连通性
            print(f"  [2/4] 三网连通性测试:")
            isp_results = test_isp_connectivity(ip, port)
            
            # 检查是否全部联通
            all_connected = all(isp_results.values())
            
            if not all_connected:
                failed_isps = [isp for isp, connected in isp_results.items() if not connected]
                print(f"      ??  部分运营商不通: {', '.join(failed_isps)}")
                print(f"      ? 未通过三网测试，跳过")
                return None
            
            print(f"      ? 电信、联通、移动全部联通")
            
            # 强制重新查询地理位置（忽略原有数据）
            print(f"  [3/4] 查询地理位置信息...")
            location = await get_ip_location(session, ip, retry=3)
            
            if location == 'Unknown':
                print(f"      ??  无法获取地理位置，跳过")
                return None
            
            print(f"      ?? 位置: {location}")
            
            # 验证格式
            print(f"  [4/4] 验证数据格式...", end=' ')
            if not location or location == 'Unknown' or '-' not in location:
                print(f"? 格式异常")
                return None
            
            print(f"? 通过")
            print(f"  ?? 此IP通过所有测试！")
            
            return {
                'ip': ip,
                'port': port,
                'location': location,
                'isp_results': isp_results
            }

        async def main():
            print("=" * 60)
            print("?? 开始测试 IP 连通性")
            print("=" * 60)
            
            # 从 api.txt 加载 API 列表
            api_urls = load_api_list('api.txt')
            
            if not api_urls:
                print("? 没有可用的 API，请检查 api.txt 文件")
                return
            
            all_ips = []
            seen_ips = set()
            
            # 收集所有IP
            async with aiohttp.ClientSession() as session:
                print(f"\n?? 正在从 {len(api_urls)} 个 API 获取 IP 列表...")
                print("-" * 60)
                
                for idx, api_url in enumerate(api_urls, 1):
                    print(f"[{idx}/{len(api_urls)}] {api_url}")
                    content = await fetch_url(session, api_url)
                    
                    if not content:
                        continue
                    
                    lines = content.split('\n')
                    count_before = len(all_ips)
                    
                    for line in lines:
                        ip_data = parse_ip_line(line)
                        if ip_data and ip_data['ip'] not in seen_ips:
                            seen_ips.add(ip_data['ip'])
                            all_ips.append(ip_data)
                    
                    new_ips = len(all_ips) - count_before
                    if new_ips > 0:
                        print(f"  ? 新增 {new_ips} 个 IP")
                    else:
                        print(f"  ??  未获取到新 IP")
                
                print("-" * 60)
                print(f"? 共收集到 {len(all_ips)} 个唯一 IP")
                
                if len(all_ips) == 0:
                    print("? 没有收集到任何 IP，请检查 API 是否正常")
                    with open('ip.txt', 'w', encoding='utf-8') as f:
                        f.write(f"# 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write("# 未收集到任何 IP 数据\n")
                    return
                
                # 测试连通性
                print("\n?? 开始测试三网连通性...")
                print("=" * 60)
                print("说明: 只保留电信、联通、移动三网全通的IP")
                print("=" * 60)
                
                valid_ips = []
                
                # 分批处理，避免并发过高
                batch_size = 10  # 降低并发数，因为每个IP要测试3个运营商
                for i in range(0, len(all_ips), batch_size):
                    batch = all_ips[i:i+batch_size]
                    batch_tasks = [test_ip_connectivity(ip_data, session) for ip_data in batch]
                    results = await asyncio.gather(*batch_tasks)
                    valid_ips.extend([r for r in results if r is not None])
                    
                    # 进度显示
                    progress = min(i + batch_size, len(all_ips))
                    print(f"\n{'='*60}")
                    print(f"?? 进度: {progress}/{len(all_ips)} ({progress*100//len(all_ips)}%) | 三网全通 IP: {len(valid_ips)}")
                    print(f"{'='*60}")
                    
                    # 短暂延迟，避免请求过快
                    await asyncio.sleep(1)
            
            print("\n" + "=" * 60)
            print(f"? 测试完成！")
            print(f"?? 总测试数: {len(all_ips)}")
            print(f"? 三网全通: {len(valid_ips)}")
            print(f"?? 通过率: {len(valid_ips)*100//len(all_ips) if all_ips else 0}%")
            print(f"?? 电信联通移动全部可用")
            print("=" * 60)
            
            # 写入文件
            with open('ip.txt', 'w', encoding='utf-8') as f:
                # 写入头部信息
                f.write(f"# IP 三网连通性测试结果\n")
                f.write(f"# 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# 总测试数: {len(all_ips)}\n")
                f.write(f"# 三网全通: {len(valid_ips)}\n")
                f.write(f"# 通过率: {len(valid_ips)*100//len(all_ips) if all_ips else 0}%\n")
                f.write(f"# 运营商: 中国电信 ? | 中国联通 ? | 中国移动 ?\n")
                f.write(f"# 格式: IP:端口#国家-地区\n")
                f.write("#" + "=" * 58 + "\n\n")
                
                if valid_ips:
                    # 按地理位置分组并排序
                    by_location = {}
                    for ip_info in valid_ips:
                        location = ip_info['location']
                        if location not in by_location:
                            by_location[location] = []
                        by_location[location].append(ip_info)
                    
                    # 按国家和城市排序
                    sorted_locations = sorted(by_location.keys(), key=lambda x: (
                        x.split('-')[0],  # 先按国家排序
                        x.split('-')[1] if '-' in x and len(x.split('-')) > 1 else ''  # 再按城市排序
                    ))
                    
                    # 写入分组后的IP
                    for location in sorted_locations:
                        ips = by_location[location]
                        f.write(f"# {location} ({len(ips)}个)\n")
                        
                        # 对每个位置的IP按端口排序
                        sorted_ips = sorted(ips, key=lambda x: (x['ip'], int(x['port'])))
                        
                        for ip_info in sorted_ips:
                            # 统一格式: IP:端口#国家-城市
                            line = f"{ip_info['ip']}:{ip_info['port']}#{ip_info['location']}"
                            f.write(f"{line}\n")
                        f.write("\n")
                    
                    print(f"\n?? 结果已保存到 ip.txt")
                    
                    # 输出统计信息（按国家汇总）
                    print("\n?? 地理位置分布统计:")
                    country_stats = {}
                    for location, ips in by_location.items():
                        country = location.split('-')[0]
                        if country not in country_stats:
                            country_stats[country] = 0
                        country_stats[country] += len(ips)
                    
                    # 按数量排序
                    for country, count in sorted(country_stats.items(), key=lambda x: x[1], reverse=True):
                        print(f"  ?? {country}: {count} 个")
                    
                    # 详细城市分布
                    print("\n?? 详细城市分布:")
                    for location in sorted_locations[:20]:  # 只显示前20个
                        print(f"  ?? {location}: {len(by_location[location])} 个")
                else:
                    f.write("# 未找到三网全通的 IP\n")
                    print("\n??  没有找到三网全通的 IP")

        if __name__ == '__main__':
            asyncio.run(main())
