import asyncio
import aiohttp
import json
import re
import socket
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Set
import time
import os
import dns.resolver
from bs4 import BeautifulSoup # 需要 'beautifulsoup4' 和 'lxml'

# --- START: 正则表达式增强 ---
# 优先匹配 IP:端口 格式，避免单独的IP正则重复匹配
IP_PORT_REGEX = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d{1,5})')
# 匹配没有被冒号跟随的独立IP (使用负向先行断言)
IP_ONLY_REGEX = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?![:\d])')
# --- END: 正则表达式增强 ---

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
        print(f"❌ {filename} 文件不存在")
        return api_urls
    
    with open(filename, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                api_urls.append(line)
    
    print(f"✅ 从 {filename} 加载了 {len(api_urls)} 个 API")
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
            if len(part) > 1 and part[0] == '0': # 排除 01, 001 等情况
                return False
        except ValueError:
            return False
    return True

def is_valid_port(port: str) -> bool:
    """验证端口号格式"""
    try:
        num = int(port)
        return 0 < num <= 65535
    except (ValueError, TypeError):
        return False

# <<< START: 新增的通用解析函数 >>>

def find_ips_in_text(text: str) -> List[Dict]:
    """在文本中查找所有IP和端口"""
    found_ips = []
    
    # 1. 查找 IP:端口
    for match in IP_PORT_REGEX.finditer(text):
        ip, port = match.groups()
        if is_valid_ip(ip) and is_valid_port(port):
            found_ips.append({'ip': ip, 'port': port})

    # 2. 查找独立的IP
    for match in IP_ONLY_REGEX.finditer(text):
        ip = match.group(1)
        if is_valid_ip(ip):
            # 避免重复添加已在 IP:端口 中找到的IP
            if not any(d['ip'] == ip for d in found_ips):
                found_ips.append({'ip': ip, 'port': '443'}) # 默认为443端口
                
    return found_ips

def parse_html_for_ips(content: str) -> List[Dict]:
    """从HTML内容中提取IP"""
    try:
        soup = BeautifulSoup(content, 'lxml')
        # 使用空格作为分隔符，防止标签间的文本粘连
        text = soup.get_text(separator=' ')
        return find_ips_in_text(text)
    except Exception as e:
        print(f"  ⚠️ HTML 解析失败: {e}")
        return []

def parse_json_for_ips(content: str) -> List[Dict]:
    """从JSON内容中递归提取IP"""
    found_ips = []
    try:
        data = json.loads(content)
        
        def extract_recursive(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    extract_recursive(value)
            elif isinstance(obj, list):
                for item in obj:
                    extract_recursive(item)
            elif isinstance(obj, str):
                # 对所有字符串值进行IP查找
                found_ips.extend(find_ips_in_text(obj))

        extract_recursive(data)
    except json.JSONDecodeError:
        # 如果JSON解析失败，尝试作为纯文本处理
        print("  ⚠️ JSON 解析失败，尝试作为纯文本处理")
        return find_ips_in_text(content)
    except Exception as e:
        print(f"  ⚠️ 处理JSON数据时出错: {e}")
    return found_ips

def parse_content_for_ips(content: str, url: str) -> List[Dict]:
    """智能解析不同类型的内容以提取IP"""
    content = content.strip()
    
    # 1. 尝试作为JSON解析
    if content.startswith('{') or content.startswith('['):
        print("  Detected JSON-like content, parsing...")
        return parse_json_for_ips(content)
        
    # 2. 尝试作为HTML解析
    if content.lower().startswith('<!doctype') or content.lower().startswith('<html'):
        print("  Detected HTML content, parsing...")
        return parse_html_for_ips(content)
        
    # 3. 作为纯文本处理
    print("  Parsing as plain text...")
    return find_ips_in_text(content)

# <<< END: 新增的通用解析函数 >>>


async def fetch_url(session: aiohttp.ClientSession, url: str) -> str:
    """异步获取URL内容"""
    try:
        # 添加通用浏览器User-Agent
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30), headers=headers) as response:
            if response.status == 200:
                # 尝试多种解码方式
                try:
                    return await response.text(encoding='utf-8')
                except UnicodeDecodeError:
                    return await response.text(encoding='gbk', errors='ignore')
            else:
                print(f"⚠️  {url} 返回状态码: {response.status}")
    except asyncio.TimeoutError:
        print(f"⏱️  {url} 请求超时")
    except Exception as e:
        print(f"❌ 获取 {url} 失败: {e}")
    return ""

def normalize_location(location: str) -> str:
    """标准化地理位置格式"""
    if not location or location == 'Unknown':
        return 'Unknown'
    
    location = location.strip()
    location = re.sub(r'\s+', ' ', location)
    
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
                        
                        if country_code and country_code in COUNTRY_MAPPING:
                            country = COUNTRY_MAPPING[country_code]
                        
                        location_parts = []
                        if country:
                            location_parts.append(country)
                        
                        if city and city.strip():
                            location_parts.append(city.strip())
                        elif region_name and region_name.strip():
                            location_parts.append(region_name.strip())
                        
                        if location_parts:
                            location = '-'.join(location_parts)
                            return normalize_location(location)
        except Exception as e:
            if attempt == retry - 1:
                print(f"    ⚠️  ip-api.com 查询失败: {e}")
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
                        
                        if city and city.strip():
                            location_parts.append(city.strip())
                        elif region and region.strip():
                            location_parts.append(region.strip())
                        
                        location = '-'.join(location_parts)
                        return normalize_location(location)
        except Exception as e:
            if attempt == retry - 1:
                print(f"    ⚠️  ipinfo.io 查询失败: {e}")
            await asyncio.sleep(1)
    
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
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [dns_server]
        resolver.timeout = 2
        resolver.lifetime = 2
        
        try:
            resolver.resolve('www.baidu.com', 'A')
        except:
            pass
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        
        result = sock.connect_ex((ip, port))
        sock.close()
        
        return result == 0
    except Exception as e:
        return False

def test_isp_connectivity(ip: str, port: int) -> Dict[str, bool]:
    """测试IP对三大运营商的连通性"""
    results = {}
    
    for isp_name, dns_servers in ISP_DNS.items():
        is_connected = False
        
        for dns_server in dns_servers:
            if test_connectivity_via_dns(ip, port, dns_server, timeout=5.0):
                is_connected = True
                break
            time.sleep(0.2)
        
        results[isp_name] = is_connected
        status = "✅" if is_connected else "❌"
        # print(f"        {status} {isp_name}: {'通' if is_connected else '不通'}")
    
    return results

async def test_ip_connectivity(ip_data: Dict, session: aiohttp.ClientSession) -> Optional[Dict]:
    """测试单个IP的连通性"""
    ip = ip_data['ip']
    port = int(ip_data['port'])
    
    # print(f"\n{'='*60}")
    # print(f"🔍 测试 IP: {ip}:{port}")
    # print(f"{'='*60}")
    
    is_connected = test_tcp_connectivity(ip, port, timeout=5.0)
    
    if not is_connected:
        # print("  [1/3] 基础连通性测试... ❌ 失败")
        return None
    
    # print("  [1/3] 基础连通性测试... ✅ 成功")
    
    isp_results = test_isp_connectivity(ip, port)
    
    all_connected = all(isp_results.values())
    
    if not all_connected:
        # failed_isps = [isp for isp, connected in isp_results.items() if not connected]
        # print(f"  [2/3] 三网连通性测试... ❌ 失败 ({', '.join(failed_isps)})")
        return None
    
    # print(f"  [2/3] 三网连通性测试... ✅ 成功")
    
    location = await get_ip_location(session, ip, retry=3)
    
    if location == 'Unknown' or '-' not in location:
        # print(f"  [3/3] 地理位置查询... ❌ 失败或格式异常")
        return None
        
    # print(f"  [3/3] 地理位置查询... ✅ 成功 ({location})")
    # print(f"  🎉 此IP通过所有测试！")
    
    return {
        'ip': ip,
        'port': port,
        'location': location,
        'isp_results': isp_results
    }

async def main():
    print("=" * 60)
    print("🚀 开始测试 IP 连通性 (增强版)")
    print("=" * 60)
    
    api_urls = load_api_list('api.txt')
    
    if not api_urls:
        print("❌ 没有可用的 API，请检查 api.txt 文件")
        return
    
    all_ips_to_test = []
    seen_ips = set()
    
    async with aiohttp.ClientSession() as session:
        print(f"\n📥 正在从 {len(api_urls)} 个 API 获取 IP 列表...")
        print("-" * 60)
        
        for idx, api_url in enumerate(api_urls, 1):
            print(f"[{idx}/{len(api_urls)}] {api_url}")
            content = await fetch_url(session, api_url)
            
            if not content:
                continue
            
            # <<< START: MODIFIED SECTION - 使用新的解析逻辑 >>>
            count_before = len(all_ips_to_test)
            
            # 调用通用解析函数
            extracted_ips = parse_content_for_ips(content, api_url)
            
            for ip_data in extracted_ips:
                # 使用 IP:端口 组合作为唯一标识
                ip_port_key = f"{ip_data['ip']}:{ip_data['port']}"
                if ip_port_key not in seen_ips:
                    seen_ips.add(ip_port_key)
                    all_ips_to_test.append(ip_data)
            # <<< END: MODIFIED SECTION >>>
            
            new_ips = len(all_ips_to_test) - count_before
            if new_ips > 0:
                print(f"  ✅ 新增 {new_ips} 个 IP")
            else:
                print(f"  ⚠️  未获取到新 IP")
        
        print("-" * 60)
        print(f"✅ 共收集到 {len(all_ips_to_test)} 个唯一 IP:端口 组合")
        
        if len(all_ips_to_test) == 0:
            print("❌ 没有收集到任何 IP，请检查 API 是否正常")
            with open('ip.txt', 'w', encoding='utf-8') as f:
                f.write(f"# 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("# 未收集到任何 IP 数据\n")
            return
        
        print("\n🔄 开始测试三网连通性...")
        print("=" * 60)
        print("说明: 只保留电信、联通、移动三网全通的IP")
        print("=" * 60)
        
        valid_ips = []
        
        batch_size = 20 # 可以适当调高并发数
        for i in range(0, len(all_ips_to_test), batch_size):
            batch = all_ips_to_test[i:i+batch_size]
            batch_tasks = [test_ip_connectivity(ip_data, session) for ip_data in batch]
            results = await asyncio.gather(*batch_tasks)
            valid_ips.extend([r for r in results if r is not None])
            
            progress = min(i + batch_size, len(all_ips_to_test))
            print(f"📊 进度: {progress}/{len(all_ips_to_test)} ({progress*100//len(all_ips_to_test)}%) | 三网全通 IP: {len(valid_ips)}")
            
            await asyncio.sleep(1)
    
    print("\n" + "=" * 60)
    print(f"✅ 测试完成！")
    print(f"📊 总测试数: {len(all_ips_to_test)}")
    print(f"✅ 三网全通: {len(valid_ips)}")
    pass_rate = (len(valid_ips) * 100 // len(all_ips_to_test)) if all_ips_to_test else 0
    print(f"📈 通过率: {pass_rate}%")
    print(f"🌐 电信联通移动全部可用")
    print("=" * 60)
    
    with open('ip.txt', 'w', encoding='utf-8') as f:
        f.write(f"# IP 三网连通性测试结果\n")
        f.write(f"# 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# 总测试数: {len(all_ips_to_test)}\n")
        f.write(f"# 三网全通: {len(valid_ips)}\n")
        f.write(f"# 通过率: {pass_rate}%\n")
        f.write(f"# 运营商: 中国电信 ✓ | 中国联通 ✓ | 中国移动 ✓\n")
        f.write(f"# 格式: IP:端口#国家-地区\n")
        f.write("#" + "=" * 58 + "\n\n")
        
        if valid_ips:
            by_location = {}
            for ip_info in valid_ips:
                location = ip_info['location']
                if location not in by_location:
                    by_location[location] = []
                by_location[location].append(ip_info)
            
            sorted_locations = sorted(by_location.keys(), key=lambda x: (
                x.split('-')[0], 
                x.split('-')[1] if '-' in x and len(x.split('-')) > 1 else ''
            ))
            
            for location in sorted_locations:
                ips = by_location[location]
                f.write(f"# {location} ({len(ips)}个)\n")
                
                sorted_ips = sorted(ips, key=lambda x: (x['ip'], int(x['port'])))
                
                for ip_info in sorted_ips:
                    line = f"{ip_info['ip']}:{ip_info['port']}#{ip_info['location']}"
                    f.write(f"{line}\n")
                f.write("\n")
            
            print(f"\n💾 结果已保存到 ip.txt")
            
            print("\n📊 地理位置分布统计:")
            country_stats = {}
            for location, ips in by_location.items():
                country = location.split('-')[0]
                country_stats[country] = country_stats.get(country, 0) + len(ips)
            
            for country, count in sorted(country_stats.items(), key=lambda x: x[1], reverse=True):
                print(f"  🌍 {country}: {count} 个")
        else:
            f.write("# 未找到三网全通的 IP\n")
            print("\n⚠️  没有找到三网全通的 IP")

if __name__ == '__main__':
    asyncio.run(main())
