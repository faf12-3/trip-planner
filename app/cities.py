"""城市清单与检索。

为什么自带一份清单，而不是每次调高德：
  1. 输入框每敲一个字就发请求，既慢又费配额；
  2. 高德的 POI 搜索不是"城市词典"，返回的是景点/机构，得再筛一遍；
  3. 演示环境可能断网——清单在本地，输入框永远能用。

清单覆盖：4 个直辖市、省会和主要旅游城市，外加拼音与首字母别名（输 nj 出南京）。
不是全量行政区划——需要精确到区县时，那是另一个功能（用高德 district 接口）。
"""

from __future__ import annotations

# 格式：城市名|全拼|首字母别名（多个用逗号）
RAW = """
    # ---- 直辖市（默认列表按此顺序展示）----
    北京|beijing|bj
    上海|shanghai|sh
    天津|tianjin|tj
    重庆|chongqing|cq
    广州|guangzhou|gz
    深圳|shenzhen|sz
    杭州|hangzhou|hz
    南京|nanjing|nj
    成都|chengdu|cd
    西安|xian|xa
    # ---- 港澳台 ----
    香港|xianggang|xg
    澳门|aomen|am
    台北|taibei|tb
    高雄|gaoxiong|gx

    # ---- 河北 ----
    石家庄|shijiazhuang|sjz
    唐山|tangshan|ts
    秦皇岛|qinhuangdao|qhd
    邯郸 邢台 保定 张家口 承德 沧州 廊坊 衡水

    # ---- 山西 ----
    太原|taiyuan|ty
    大同|datong|dt
    阳泉 长治 晋城 朔州 晋中 运城 忻州 临汾 吕梁

    # ---- 内蒙古 ----
    呼和浩特|huhehaote|hhht
    包头|baotou|bt
    乌海 赤峰 通辽 鄂尔多斯 呼伦贝尔 巴彦淖尔 乌兰察布

    # ---- 辽宁 ----
    沈阳|shenyang|sy2
    大连|dalian|dl2
    鞍山 抚顺 本溪 丹东 锦州 营口 阜新 辽阳 盘锦 铁岭 朝阳 葫芦岛

    # ---- 吉林 ----
    长春|changchun|cc
    吉林|jilin|jl
    四平 辽源 通化 白山 松原 白城 延边

    # ---- 黑龙江 ----
    哈尔滨|haerbin|heb
    齐齐哈尔|qiqihaer|qqhe
    鸡西 鹤岗 双鸭山 大庆 伊春 佳木斯 七台河 牡丹江 黑河 绥化

    # ---- 江苏 ----
    南京|nanjing|nj
    苏州|suzhou|sz2
    无锡|wuxi|wx
    常州|changzhou|cz
    南通|nantong|nt
    徐州|xuzhou|xz
    扬州|yangzhou|yz
    镇江|zhenjiang|zj
    泰州|taizhou|tz
    连云港 淮安 盐城 宿迁

    # ---- 浙江 ----
    杭州|hangzhou|hz
    宁波|ningbo|nb
    温州|wenzhou|wz
    绍兴|shaoxing|sx
    金华|jinhua|jh
    嘉兴|jiaxing|jx
    湖州|huzhou|hz4
    衢州 舟山 台州 丽水

    # ---- 安徽 ----
    合肥|hefei|hf
    黄山|huangshan|hs
    芜湖|wuhu|wh
    蚌埠 淮南 马鞍山 淮北 铜陵 安庆 滁州 阜阳 宿州 六安 亳州 池州 宣城

    # ---- 福建 ----
    福州|fuzhou|fz
    厦门|xiamen|xm
    泉州|quanzhou|qz
    莆田 三明 漳州 南平 龙岩 宁德

    # ---- 江西 ----
    南昌|nanchang|nc
    九江|jiujiang|jj
    景德镇|jingdezhen|jdz
    萍乡 新余 鹰潭 赣州 吉安 宜春 抚州 上饶

    # ---- 山东 ----
    济南|jinan|jn
    青岛|qingdao|qd
    烟台|yantai|yt
    威海|weihai|wh2
    泰安|taian|ta
    曲阜|qufu|qf
    日照|rizhao|rz
    济宁|jining|jn2
    枣庄 东营 潍坊 临沂 德州 聊城 滨州 菏泽

    # ---- 河南 ----
    郑州|zhengzhou|zz
    洛阳|luoyang|ly
    开封|kaifeng|kf
    平顶山 安阳 鹤壁 新乡 焦作 濮阳 许昌 漯河 三门峡 南阳 商丘 信阳 周口 驻马店

    # ---- 湖北 ----
    武汉|wuhan|wh3
    宜昌|yichang|yc
    荆州|jingzhou|jz
    襄阳|xiangyang|xy2
    黄石 十堰 鄂州 荆门 孝感 黄冈 咸宁 随州 恩施

    # ---- 湖南 ----
    长沙|changsha|cs
    张家界|zhangjiajie|zjj
    岳阳|yueyang|yy
    株洲 湘潭 衡阳 邵阳 常德 益阳 郴州 永州 怀化 娄底 湘西

    # ---- 广东 ----
    广州|guangzhou|gz
    深圳|shenzhen|sz
    珠海|zhuhai|zh
    佛山|foshan|fs
    东莞|dongguan|dg
    中山|zhongshan|zs
    惠州|huizhou|hz2
    汕头|shantou|st
    湛江|zhanjiang|zj2
    韶关 江门 茂名 肇庆 梅州 汕尾 河源 阳江 清远 潮州 揭阳 云浮

    # ---- 广西 ----
    南宁|nanning|nn
    桂林|guilin|gl
    北海|beihai|bh
    柳州 梧州 防城港 钦州 贵港 玉林 百色 贺州 河池 来宾 崇左

    # ---- 海南 ----
    海口|haikou|hk
    三亚|sanya|sy
    儋州 琼海 文昌 万宁

    # ---- 四川 ----
    成都|chengdu|cd
    绵阳|mianyang|my
    乐山|leshan|ls
    九寨沟|jiuzhaigou|jzg
    峨眉山|emeishan|ems
    都江堰|dujiangyan|djy
    自贡 攀枝花 泸州 德阳 广元 遂宁 内江 南充 眉山 宜宾 广安 达州 雅安 巴中 资阳
    阿坝 甘孜 凉山

    # ---- 贵州 ----
    贵阳|guiyang|gy
    遵义|zunyi|zy
    六盘水 安顺 毕节 铜仁 黔东南 黔南 黔西南

    # ---- 云南 ----
    昆明|kunming|km
    大理|dali|dl
    丽江|lijiang|lj
    西双版纳|xishuangbanna|xsbn
    曲靖 玉溪 保山 昭通 普洱 临沧 楚雄 红河 文山 德宏 怒江 迪庆

    # ---- 西藏 ----
    拉萨|lasa|ls2
    日喀则|rikaze|rzk
    林芝|linzhi|lz2
    昌都 山南 那曲 阿里

    # ---- 陕西 ----
    西安|xian|xa
    咸阳|xianyang|xy
    宝鸡|baoji|bj2
    延安|yanan|ya
    汉中|hanzhong|hz3
    铜川 渭南 榆林 安康 商洛

    # ---- 甘肃 ----
    兰州|lanzhou|lz
    敦煌|dunhuang|dh
    嘉峪关|jiayuguan|jyg
    天水|tianshui|ts2
    张掖|zhangye|zy2
    酒泉|jiuquan|jq
    金昌 白银 武威 平凉 庆阳 定西 陇南 临夏 甘南

    # ---- 青海 ----
    西宁|xining|xn
    海东|haidong|hd
    格尔木|geermu|gem
    海北 黄南 海南州 果洛 玉树 海西

    # ---- 宁夏 ----
    银川|yinchuan|yc2
    石嘴山|shizuishan|szs
    吴忠 固原 中卫

    # ---- 新疆 ----
    乌鲁木齐|wulumuqi|wlmq
    喀什|kashi|ks
    克拉玛依|kelamayi|klmy
    吐鲁番|tulufan|tlf
    伊犁|yili|yl
    哈密 昌吉 博尔塔拉 巴音郭楞 阿克苏 克州 和田 塔城 阿勒泰
"""

_CITIES: list[dict[str, str]] | None = None


def _parse() -> list[dict[str, str]]:
    global _CITIES
    if _CITIES is not None:
        return _CITIES

    out: list[dict[str, str]] = []
    index: dict[str, dict[str, str]] = {}
    province = "其他"

    def add(name: str, pinyin: str = "", initials: str = "") -> None:
        name = name.strip()
        if not name:
            return
        if name in index:
            # 热门组里的城市同时属于真实省份：以真实省份为准，
            # 否则"广东"分组里看不到广州。
            entry = index[name]
            if entry["province"] == "直辖市" and province != "直辖市":
                entry["province"] = province
            return
        entry = {
            "name": name,
            "pinyin": pinyin.lower().strip(),
            "initials": initials.lower().strip(),
            "province": province,
        }
        index[name] = entry
        out.append(entry)

    for line in RAW.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            # `# ---- 河北 ----` -> 河北；`# ---- 热门城市（…）----` -> 热门城市
            label = line.lstrip("#- ").rstrip("- ").strip()
            label = label.split("（")[0].strip()
            province = label or province
            continue
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            add(parts[0], *(parts[1:3] + ["", ""])[:2])
        else:
            for token in line.replace("、", " ").split():
                add(token)
    _CITIES = out
    return out


def all_cities() -> list[dict[str, str]]:
    return list(_parse())


def _score(city: dict[str, str], q: str) -> int:
    """越小越靠前。

    关键规则：**两个字母以内的查询不做子串匹配**。
    否则 "nj" 会命中「天津」（tia-nj-in）、「镇江」（zhe-nj-iang），下拉里全是噪音。
    长查询仍允许全拼子串，这样 "jing" 能搜到「北京」「南京」——
    那是真有用的模糊匹配，不是噪音。
    """
    name = city["name"]
    pinyin = city["pinyin"]
    initials = city["initials"]

    if name == q:
        return 0
    if name.startswith(q):
        return 1
    if pinyin.startswith(q) or initials.startswith(q):
        return 2
    if q in name:
        return 3

    short = len(q) <= 2 and q.isascii() and q.isalpha()
    if short:
        return 99
    if q in pinyin:
        return 4
    return 99


def search(q: str = "", limit: int = 8) -> list[dict[str, str]]:
    """按关键词检索城市；空关键词返回前 limit 个热门城市。"""
    limit = max(1, min(int(limit or 8), 400))  # 清单 344 个：空查询要能一次取完
    query = (q or "").strip().lower()
    cities = _parse()

    if not query:
        return cities[:limit]

    scored = [(_score(c, query), i, c) for i, c in enumerate(cities)]
    hits = [item for item in scored if item[0] < 99]
    hits.sort(key=lambda item: (item[0], item[1]))
    return [c for _, _, c in hits[:limit]]


def grouped(q: str = "", limit: int = 400) -> list[dict[str, object]]:
    """按省份分组返回，保持首次出现顺序（热门城市在最前）。"""
    buckets: dict[str, list[dict[str, str]]] = {}
    for city in search(q, limit=limit):
        buckets.setdefault(city["province"], []).append(city)
    return [
        {"province": name, "cities": cities, "count": len(cities)}
        for name, cities in buckets.items()
    ]
