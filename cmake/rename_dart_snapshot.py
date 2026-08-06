#!/usr/bin/env python3
# ============================================================
# rename_dart_snapshot.py
# ------------------------------------------------------------
# 给单个插件的 Dart AOT 快照符号改成「每插件唯一」的名字，从根本上消除
# 多个基于本模板的 AOT 插件在同一宿主进程内「UI 串台」的问题（仅 macOS）。
#
# 根因（macOS AOT）：
#   flutter build macos --release 生成的 App.framework 全局导出四个 Dart
#   快照符号（_kDartVmSnapshotData / _kDartVmSnapshotInstructions /
#   _kDartIsolateSnapshotData / _kDartIsolateSnapshotInstructions）。
#   FlutterMacOS 引擎用 dlsym(RTLD_DEFAULT, "kDart...") 这种「进程级扁平
#   符号查找」定位快照，按加载顺序返回第一个匹配 → 两个插件同名符号时，
#   后加载插件的引擎解析到先加载插件的 isolate 快照 → 显示错误 UI。
#   （dlsym interpose 在现代 macOS 对 dlopen 的库不生效，install name
#    唯一化也无效，因为 RTLD_DEFAULT 是按符号名解析的。）
#   注：Windows/Linux 通过 aot_library_path / libapp.so 按「显式路径」加载
#   AOT，天然按插件隔离，不存在此问题，故本脚本只用于 macOS。
#
# 解决：把每个插件的四个快照符号名里的共享前缀 "kDart" 换成一个「由
# PROJECT_NAME 派生、每插件唯一」的同长度 tag，并同步修改该插件自带的
# FlutterMacOS_<name> 引擎里请求这些符号的字符串，使两个插件符号名不再
# 相同 → RTLD_DEFAULT 不再串台。
#
# 为什么 tag 是 5 个字符：
#   四个符号在 dlsym 使用的「导出 trie」里共享唯一的一条前缀边 "_kDart"
#   （6 字节）。原地字节替换要求同长度，且开头的 "_"（Mach-O 符号下划线，
#   dlsym 会自动补）必须保留，故可改的只有其后的 "kDart" 5 个字节 → tag
#   固定 5 个字符。用 base-62 字母表（0-9A-Za-z）→ 62^5 ≈ 9.16 亿种组合，
#   对任何现实数量的插件都可视为零碰撞（birthday 50% 需约 3 万个插件）。
#
# 用法：
#   rename_dart_snapshot.py --role app    --project-name <Name> <App二进制>
#   rename_dart_snapshot.py --role engine --project-name <Name> <FlutterMacOS二进制>
# ============================================================
import struct
import hashlib
import argparse

# 被替换的共享前缀（5 字节，四个符号共有）。tag 亦为 5 字节。
SHARED_PREFIX = b"kDart"

SNAPSHOT_BASES = (
    b"VmSnapshotData",
    b"VmSnapshotInstructions",
    b"IsolateSnapshotData",
    b"IsolateSnapshotInstructions",
)

_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

FAT_MAGICS = (0xCAFEBABE, 0xBEBAFECA)
MH_MAGICS_LE = (0xFEEDFACF, 0xFEEDFACE)


def derive_tag(project_name):
    """由 PROJECT_NAME 确定性派生 5 字符 base-62 tag（与 "kDart" 等长）。"""
    n = int.from_bytes(hashlib.sha256(project_name.encode("utf-8")).digest(), "big")
    out = []
    for _ in range(len(SHARED_PREFIX)):  # 5
        out.append(_BASE62[n % 62])
        n //= 62
    return "".join(out).encode("ascii")


def fat_slices(data):
    magic = struct.unpack(">I", data[:4])[0]
    if magic in FAT_MAGICS:
        n = struct.unpack(">I", data[4:8])[0]
        out = []
        for i in range(n):
            off = 8 + i * 20
            o = struct.unpack(">I", data[off + 8:off + 12])[0]
            sz = struct.unpack(">I", data[off + 12:off + 16])[0]
            out.append((o, sz))
        return out
    return [(0, len(data))]


def _u32(b, o, le):
    return struct.unpack("<I" if le else ">I", b[o:o + 4])[0]


def app_rewrite_regions(data, base):
    """返回 (offset, size) 列表：导出 trie 区 + 符号表字符串区。"""
    magic = struct.unpack("<I", data[base:base + 4])[0]
    le = magic in MH_MAGICS_LE
    ncmds = _u32(data, base + 16, le)
    off = base + 32
    regs = []
    LC_SYMTAB = 0x2
    LC_DYLD_INFO = 0x22
    LC_DYLD_INFO_ONLY = 0x80000022
    LC_DYLD_EXPORTS_TRIE = 0x80000033
    for _ in range(ncmds):
        cmd = _u32(data, off, le)
        sz = _u32(data, off + 4, le)
        if cmd in (LC_DYLD_INFO, LC_DYLD_INFO_ONLY):
            eo = _u32(data, off + 40, le)
            es = _u32(data, off + 44, le)
            if es:
                regs.append((base + eo, es))
        elif cmd == LC_DYLD_EXPORTS_TRIE:
            do = _u32(data, off + 8, le)
            ds = _u32(data, off + 12, le)
            if ds:
                regs.append((base + do, ds))
        elif cmd == LC_SYMTAB:
            stroff = _u32(data, off + 16, le)
            strsize = _u32(data, off + 20, le)
            if strsize:
                regs.append((base + stroff, strsize))
        off += sz
    return regs


def replace_in_range(data, start, end, old, new):
    assert len(old) == len(new)
    i = start
    count = 0
    while True:
        j = data.find(old, i, end)
        if j < 0:
            break
        data[j:j + len(old)] = new
        i = j + len(old)
        count += 1
    return count


def patch_app(path, tag):
    # App 的四个符号共享前缀 "_kDart"；保留开头的 "_"，把其后的 "kDart" 换成 tag。
    old = b"_" + SHARED_PREFIX
    new = b"_" + tag
    assert len(old) == len(new)
    data = bytearray(open(path, "rb").read())
    total = 0
    for (o, _sz) in fat_slices(data):
        for (a, l) in app_rewrite_regions(data, o):
            total += replace_in_range(data, a, a + l, old, new)
    if total == 0:
        raise SystemExit("ERROR: 在 App 二进制的导出 trie/符号表中未找到 _kDart，改名失败")
    open(path, "wb").write(data)
    print("[rename] app  %s: _%s -> _%s  (%d 处)"
          % (path, SHARED_PREFIX.decode(), tag.decode(), total))


def patch_engine(path, tag):
    data = bytearray(open(path, "rb").read())
    total = 0
    for base in SNAPSHOT_BASES:
        old = SHARED_PREFIX + base
        new = tag + base
        assert len(old) == len(new)
        total += replace_in_range(data, 0, len(data), old, new)
    if total == 0:
        raise SystemExit("ERROR: 在引擎二进制中未找到 kDart 快照字符串，改名失败")
    open(path, "wb").write(data)
    print("[rename] engine %s: %s -> %s  (%d 处)"
          % (path, SHARED_PREFIX.decode(), tag.decode(), total))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True, choices=("app", "engine"))
    ap.add_argument("--project-name", required=True,
                    help="插件工程名，用于确定性派生每插件唯一 tag")
    ap.add_argument("binary")
    args = ap.parse_args()
    tag = derive_tag(args.project_name)
    assert len(tag) == len(SHARED_PREFIX)
    if args.role == "app":
        patch_app(args.binary, tag)
    else:
        patch_engine(args.binary, tag)


if __name__ == "__main__":
    main()
