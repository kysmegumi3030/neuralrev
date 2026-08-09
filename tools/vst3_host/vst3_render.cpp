// =============================================================================
// vst3_render.cpp — 参考插件（VST3 bundle）离线渲染器 / 参数探测器
// -----------------------------------------------------------------------------
// 为什么需要它（不用 pedalboard）：
//   参考插件 Tone King Imperial MKII.vst3 由 PACE/iLok（__Pace_Eden）包裹。
//   在 Python 进程（pedalboard / juce_vst3_helper）里加载会被 **SIGKILL**（exit 137，
//   无任何异常、无 crash log）——PACE 的反调试/宿主校验拒绝该宿主进程。
//   但同一 bundle 在**原生可执行文件**里 dlopen + GetPluginFactory 完全正常
//   （已实测：bundleEntry -> 1、factory 指针有效）。
//   所以参考侧渲染必须走原生宿主，本工具即是。
//
// 它讲的是 plugin_match.OfflineRenderer 的同一套 f32 stdin/stdout 协议，
// 于是「参考」和「候选」在 Python 侧是同一个类，A/B 代码零分叉。
//
// 用法：
//   probe  模式:  vst3_render --plugin <bundle> --probe
//                   打印全部参数 JSON：id / title / units / stepCount /
//                   defaultNormalized / flags / 单位串
//   sweep  模式:  vst3_render --plugin <bundle> --sweep <pid> [--steps 21]
//                   扫描某参数的显示串（求真实数值范围 / 档位）
//   render 模式:  vst3_render --plugin <bundle> --sr 48000 --block 512 --nch 2 \
//                            [--param <pid>=<norm>]... [--tail 0] < in.f32 > out.f32
//                   stdin/stdout 均为 interleaved float32
//
// 参数值一律是 VST3 normalized（0..1），与 plugin_match 的约定一致。
// =============================================================================
#include "pluginterfaces/base/funknown.h"
#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/base/ipluginbase.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivsthostapplication.h"
#include "pluginterfaces/vst/ivstmessage.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "pluginterfaces/vst/ivstprocesscontext.h"
#include "pluginterfaces/vst/vsttypes.h"

#include <dlfcn.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

using namespace Steinberg;
using namespace Steinberg::Vst;

// -----------------------------------------------------------------------------
// VST3 接口 IID 的实体定义。
// SDK 头文件只用 DECLARE_CLASS_IID 声明 iid 静态成员，实体必须由**宿主侧的某一个**
// 编译单元用 DEF_CLASS_IID 给出（插件侧同理，各自一份，互不影响）。
// 这里集中定义本宿主实际 query/create 的全部接口。
// -----------------------------------------------------------------------------
DEF_CLASS_IID(IComponent)
DEF_CLASS_IID(IAudioProcessor)
DEF_CLASS_IID(IEditController)
DEF_CLASS_IID(IConnectionPoint)
DEF_CLASS_IID(IComponentHandler)
DEF_CLASS_IID(IHostApplication)
DEF_CLASS_IID(IMessage)
DEF_CLASS_IID(IAttributeList)
DEF_CLASS_IID(IParameterChanges)
DEF_CLASS_IID(IParamValueQueue)

namespace
{

// -----------------------------------------------------------------------------
// String128（UTF-16）→ std::string（仅取 ASCII 可见字符，够用于参数名/单位/显示串）
// -----------------------------------------------------------------------------
std::string fromString128(const String128 s)
{
    std::string out;
    for (int i = 0; i < 128 && s[i] != 0; ++i)
    {
        const auto c = static_cast<unsigned int>(s[i]);
        out.push_back(c < 128 ? static_cast<char>(c) : '?');
    }
    return out;
}

std::string jsonEscape(const std::string& s)
{
    std::string out;
    for (char c : s)
    {
        switch (c)
        {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:
                if (static_cast<unsigned char>(c) < 0x20)
                    out += ' ';
                else
                    out += c;
        }
    }
    return out;
}

// =============================================================================
// 最小宿主实现
// -----------------------------------------------------------------------------
// VST3 插件在 initialize() 时会 queryInterface 宿主的 IHostApplication，
// 并（Neural DSP 这类插件常见）通过 IComponentHandler 回报参数变化、
// 通过 IMessage/IAttributeList 在 component 与 controller 间通信。
// 这里只实现离线渲染真正需要的部分：
//   * IHostApplication::getName / createInstance(IMessage, IAttributeList)
//   * IComponentHandler：三个 edit 回调全部空实现（不做自动化回写）
//   * IConnectionPoint 直连：component <-> controller 互发消息
// =============================================================================

// ---- IAttributeList 最小实现（消息负载容器）----
class HostAttributeList final : public IAttributeList
{
public:
    tresult PLUGIN_API setInt(AttrID id, int64 value) override { ints_[id] = value; return kResultOk; }
    tresult PLUGIN_API getInt(AttrID id, int64& value) override
    {
        auto it = ints_.find(id);
        if (it == ints_.end()) return kResultFalse;
        value = it->second;
        return kResultOk;
    }
    tresult PLUGIN_API setFloat(AttrID id, double value) override { floats_[id] = value; return kResultOk; }
    tresult PLUGIN_API getFloat(AttrID id, double& value) override
    {
        auto it = floats_.find(id);
        if (it == floats_.end()) return kResultFalse;
        value = it->second;
        return kResultOk;
    }
    tresult PLUGIN_API setString(AttrID id, const TChar* string) override
    {
        std::vector<TChar> v;
        for (const TChar* p = string; p && *p; ++p) v.push_back(*p);
        v.push_back(0);
        strings_[id] = std::move(v);
        return kResultOk;
    }
    tresult PLUGIN_API getString(AttrID id, TChar* string, uint32 sizeInBytes) override
    {
        auto it = strings_.find(id);
        if (it == strings_.end()) return kResultFalse;
        const uint32 maxChars = sizeInBytes / sizeof(TChar);
        if (maxChars == 0) return kResultFalse;
        uint32 n = std::min<uint32>(static_cast<uint32>(it->second.size()), maxChars);
        std::memcpy(string, it->second.data(), n * sizeof(TChar));
        string[n - 1] = 0;
        return kResultOk;
    }
    tresult PLUGIN_API setBinary(AttrID id, const void* data, uint32 sizeInBytes) override
    {
        const auto* p = static_cast<const uint8*>(data);
        bins_[id].assign(p, p + sizeInBytes);
        return kResultOk;
    }
    tresult PLUGIN_API getBinary(AttrID id, const void*& data, uint32& sizeInBytes) override
    {
        auto it = bins_.find(id);
        if (it == bins_.end()) return kResultFalse;
        data = it->second.data();
        sizeInBytes = static_cast<uint32>(it->second.size());
        return kResultOk;
    }

    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override
    {
        QUERY_INTERFACE(iid, obj, FUnknown::iid, IAttributeList)
        QUERY_INTERFACE(iid, obj, IAttributeList::iid, IAttributeList)
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return ++refs_; }
    uint32 PLUGIN_API release() override
    {
        if (--refs_ == 0) { delete this; return 0; }
        return refs_;
    }

private:
    std::map<std::string, int64> ints_;
    std::map<std::string, double> floats_;
    std::map<std::string, std::vector<TChar>> strings_;
    std::map<std::string, std::vector<uint8>> bins_;
    uint32 refs_ { 1 };
};

// ---- IMessage 最小实现 ----
class HostMessage final : public IMessage
{
public:
    HostMessage() : attrs_(new HostAttributeList()) {}
    ~HostMessage() { if (attrs_) attrs_->release(); }

    const char* PLUGIN_API getMessageID() override { return id_.c_str(); }
    void PLUGIN_API setMessageID(const char* id) override { id_ = id ? id : ""; }
    IAttributeList* PLUGIN_API getAttributes() override { return attrs_; }

    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override
    {
        QUERY_INTERFACE(iid, obj, FUnknown::iid, IMessage)
        QUERY_INTERFACE(iid, obj, IMessage::iid, IMessage)
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return ++refs_; }
    uint32 PLUGIN_API release() override
    {
        if (--refs_ == 0) { delete this; return 0; }
        return refs_;
    }

private:
    std::string id_;
    HostAttributeList* attrs_ { nullptr };
    uint32 refs_ { 1 };
};

// ---- IHostApplication + IComponentHandler ----
class HostApp final : public IHostApplication, public IComponentHandler
{
public:
    tresult PLUGIN_API getName(String128 name) override
    {
        const char* n = "neuralrev vst3_render";
        int i = 0;
        for (; n[i] && i < 127; ++i) name[i] = static_cast<char16>(n[i]);
        name[i] = 0;
        return kResultOk;
    }

    tresult PLUGIN_API createInstance(TUID cid, TUID _iid, void** obj) override
    {
        const FUID classID(FUID::fromTUID(cid));
        const FUID interfaceID(FUID::fromTUID(_iid));
        if (classID == IMessage::iid && interfaceID == IMessage::iid)
        {
            *obj = new HostMessage();
            return kResultOk;
        }
        if (classID == IAttributeList::iid && interfaceID == IAttributeList::iid)
        {
            *obj = new HostAttributeList();
            return kResultOk;
        }
        *obj = nullptr;
        return kResultFalse;
    }

    // IComponentHandler：离线渲染不需要把插件侧的改动回写宿主，全部接受即可。
    tresult PLUGIN_API beginEdit(ParamID) override { return kResultOk; }
    tresult PLUGIN_API performEdit(ParamID, ParamValue) override { return kResultOk; }
    tresult PLUGIN_API endEdit(ParamID) override { return kResultOk; }
    tresult PLUGIN_API restartComponent(int32) override { return kResultOk; }

    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override
    {
        QUERY_INTERFACE(iid, obj, FUnknown::iid, IHostApplication)
        QUERY_INTERFACE(iid, obj, IHostApplication::iid, IHostApplication)
        QUERY_INTERFACE(iid, obj, IComponentHandler::iid, IComponentHandler)
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }
};

// =============================================================================
// IParameterChanges 最小实现：每个渲染块把「本块要设的参数值」喂给插件。
// 一个参数一个 queue，每 queue 一个点（sampleOffset 0），即块首阶跃。
// =============================================================================
class ParamValueQueue final : public IParamValueQueue
{
public:
    void configure(ParamID id) { id_ = id; points_.clear(); }
    void addPointDirect(int32 offset, ParamValue v) { points_.push_back({ offset, v }); }

    ParamID PLUGIN_API getParameterId() override { return id_; }
    int32 PLUGIN_API getPointCount() override { return static_cast<int32>(points_.size()); }
    tresult PLUGIN_API getPoint(int32 index, int32& sampleOffset, ParamValue& value) override
    {
        if (index < 0 || index >= static_cast<int32>(points_.size())) return kResultFalse;
        sampleOffset = points_[static_cast<size_t>(index)].first;
        value = points_[static_cast<size_t>(index)].second;
        return kResultOk;
    }
    tresult PLUGIN_API addPoint(int32 sampleOffset, ParamValue value, int32& index) override
    {
        points_.push_back({ sampleOffset, value });
        index = static_cast<int32>(points_.size()) - 1;
        return kResultOk;
    }

    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override
    {
        QUERY_INTERFACE(iid, obj, FUnknown::iid, IParamValueQueue)
        QUERY_INTERFACE(iid, obj, IParamValueQueue::iid, IParamValueQueue)
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }

private:
    ParamID id_ { 0 };
    std::vector<std::pair<int32, ParamValue>> points_;
};

class ParameterChanges final : public IParameterChanges
{
public:
    // 用「参数 → 归一值」表铺一遍队列（每块调用一次；空表则本块无自动化）
    void setValues(const std::map<ParamID, ParamValue>& values)
    {
        queues_.resize(values.size());
        size_t i = 0;
        for (const auto& kv : values)
        {
            queues_[i].configure(kv.first);
            queues_[i].addPointDirect(0, kv.second);
            ++i;
        }
        count_ = static_cast<int32>(values.size());
    }
    void clear() { count_ = 0; }

    int32 PLUGIN_API getParameterCount() override { return count_; }
    IParamValueQueue* PLUGIN_API getParameterData(int32 index) override
    {
        if (index < 0 || index >= count_) return nullptr;
        return &queues_[static_cast<size_t>(index)];
    }
    IParamValueQueue* PLUGIN_API addParameterData(const ParamID&, int32&) override { return nullptr; }

    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override
    {
        QUERY_INTERFACE(iid, obj, FUnknown::iid, IParameterChanges)
        QUERY_INTERFACE(iid, obj, IParameterChanges::iid, IParameterChanges)
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }

private:
    std::vector<ParamValueQueue> queues_;
    int32 count_ { 0 };
};

// =============================================================================
// 插件加载：dlopen bundle 内的可执行文件 → bundleEntry → GetPluginFactory
// -----------------------------------------------------------------------------
// 不用 SDK 的 hosting/module_mac.mm（它走 CFBundle 且要额外编译单元），
// 直接 dlopen 已实测可用（PACE 也接受）。
// =============================================================================
struct LoadedModule
{
    void* handle { nullptr };
    IPluginFactory* factory { nullptr };
};

std::string bundleExecutablePath(const std::string& bundlePath)
{
    // <bundle>.vst3/Contents/MacOS/<name>
    std::string name = bundlePath;
    if (!name.empty() && name.back() == '/') name.pop_back();
    const auto slash = name.find_last_of('/');
    if (slash != std::string::npos) name = name.substr(slash + 1);
    const auto dot = name.rfind(".vst3");
    if (dot != std::string::npos) name = name.substr(0, dot);

    std::string base = bundlePath;
    if (!base.empty() && base.back() == '/') base.pop_back();
    return base + "/Contents/MacOS/" + name;
}

bool loadModule(const std::string& bundlePath, LoadedModule& out)
{
    const std::string exe = bundleExecutablePath(bundlePath);
    out.handle = dlopen(exe.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (out.handle == nullptr)
    {
        std::fprintf(stderr, "[vst3_render] dlopen failed: %s\n", dlerror());
        return false;
    }

    using BundleEntryFn = bool (*)(void*);
    if (auto* entry = reinterpret_cast<BundleEntryFn>(dlsym(out.handle, "bundleEntry")))
    {
        if (!entry(nullptr))
        {
            std::fprintf(stderr, "[vst3_render] bundleEntry returned false\n");
            return false;
        }
    }

    using GetFactoryFn = IPluginFactory* (*)();
    auto* getFactory = reinterpret_cast<GetFactoryFn>(dlsym(out.handle, "GetPluginFactory"));
    if (getFactory == nullptr)
    {
        std::fprintf(stderr, "[vst3_render] GetPluginFactory not found\n");
        return false;
    }
    out.factory = getFactory();
    if (out.factory == nullptr)
    {
        std::fprintf(stderr, "[vst3_render] factory is null\n");
        return false;
    }
    return true;
}

// -----------------------------------------------------------------------------
// 实例化第一个 Audio Module Class（kVstAudioEffectClass），拿到 component +
// controller，接好 connection point，component 状态同步给 controller。
// -----------------------------------------------------------------------------
struct PluginInstance
{
    IComponent* component { nullptr };
    IAudioProcessor* processor { nullptr };
    IEditController* controller { nullptr };
    IConnectionPoint* compConn { nullptr };
    IConnectionPoint* ctrlConn { nullptr };
    std::string className;
};

// 直连两个 connection point：一端 notify 直接转投另一端。
class ConnectionProxy final : public IConnectionPoint
{
public:
    ConnectionProxy(IConnectionPoint* dest) : dest_(dest) {}

    tresult PLUGIN_API connect(IConnectionPoint*) override { return kResultOk; }
    tresult PLUGIN_API disconnect(IConnectionPoint*) override { return kResultOk; }
    tresult PLUGIN_API notify(IMessage* message) override
    {
        return dest_ ? dest_->notify(message) : kResultOk;
    }

    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override
    {
        QUERY_INTERFACE(iid, obj, FUnknown::iid, IConnectionPoint)
        QUERY_INTERFACE(iid, obj, IConnectionPoint::iid, IConnectionPoint)
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }

private:
    IConnectionPoint* dest_ { nullptr };
};

// 简易可读写内存流（用于 component → controller 的状态同步）
class MemStream final : public IBStream
{
public:
    tresult PLUGIN_API read(void* buffer, int32 numBytes, int32* numBytesRead) override
    {
        const int32 avail = static_cast<int32>(data_.size()) - pos_;
        const int32 n = std::max(0, std::min(numBytes, avail));
        if (n > 0) std::memcpy(buffer, data_.data() + pos_, static_cast<size_t>(n));
        pos_ += n;
        if (numBytesRead) *numBytesRead = n;
        return kResultOk;
    }
    tresult PLUGIN_API write(void* buffer, int32 numBytes, int32* numBytesWritten) override
    {
        const auto* p = static_cast<const uint8*>(buffer);
        if (pos_ != static_cast<int32>(data_.size())) data_.resize(static_cast<size_t>(pos_));
        data_.insert(data_.end(), p, p + numBytes);
        pos_ += numBytes;
        if (numBytesWritten) *numBytesWritten = numBytes;
        return kResultOk;
    }
    tresult PLUGIN_API seek(int64 pos, int32 mode, int64* result) override
    {
        int64 target = pos;
        if (mode == kIBSeekCur) target = pos_ + pos;
        else if (mode == kIBSeekEnd) target = static_cast<int64>(data_.size()) + pos;
        pos_ = static_cast<int32>(std::max<int64>(0, std::min<int64>(target, static_cast<int64>(data_.size()))));
        if (result) *result = pos_;
        return kResultOk;
    }
    tresult PLUGIN_API tell(int64* pos) override
    {
        if (pos) *pos = pos_;
        return kResultOk;
    }
    void rewind() { pos_ = 0; }

    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override
    {
        QUERY_INTERFACE(iid, obj, FUnknown::iid, IBStream)
        QUERY_INTERFACE(iid, obj, IBStream::iid, IBStream)
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }

private:
    std::vector<uint8> data_;
    int32 pos_ { 0 };
};

HostApp g_host;

bool instantiate(IPluginFactory* factory, PluginInstance& inst)
{
    PFactoryInfo factoryInfo {};
    factory->getFactoryInfo(&factoryInfo);

    // factory2/3 才能拿到 IHostApplication 注入接口（PACE 插件通常要求 factory3）
    if (auto* f3 = FUnknownPtr<IPluginFactory3>(factory).getInterface())
        f3->setHostContext(static_cast<IHostApplication*>(&g_host));

    const int32 n = factory->countClasses();
    for (int32 i = 0; i < n; ++i)
    {
        PClassInfo ci {};
        if (factory->getClassInfo(i, &ci) != kResultOk) continue;
        if (std::strcmp(ci.category, kVstAudioEffectClass) != 0) continue;

        IComponent* comp = nullptr;
        if (factory->createInstance(ci.cid, IComponent::iid, reinterpret_cast<void**>(&comp)) != kResultOk
            || comp == nullptr)
            continue;

        if (comp->initialize(static_cast<IHostApplication*>(&g_host)) != kResultOk)
        {
            comp->release();
            continue;
        }

        auto* proc = FUnknownPtr<IAudioProcessor>(comp).getInterface();
        if (proc == nullptr)
        {
            comp->terminate();
            comp->release();
            continue;
        }
        proc->addRef();

        // controller：优先 component 自带，其次按 controllerClassId 创建
        IEditController* ctrl = FUnknownPtr<IEditController>(comp).getInterface();
        if (ctrl != nullptr)
        {
            ctrl->addRef();
        }
        else
        {
            TUID ctrlId;
            if (comp->getControllerClassId(ctrlId) == kResultOk)
                factory->createInstance(ctrlId, IEditController::iid, reinterpret_cast<void**>(&ctrl));
            if (ctrl != nullptr && ctrl->initialize(static_cast<IHostApplication*>(&g_host)) != kResultOk)
            {
                ctrl->release();
                ctrl = nullptr;
            }
        }

        if (ctrl != nullptr)
        {
            ctrl->setComponentHandler(static_cast<IComponentHandler*>(&g_host));

            // connection point 直连（Neural DSP 插件依赖它同步内部状态）
            inst.compConn = FUnknownPtr<IConnectionPoint>(comp).getInterface();
            inst.ctrlConn = FUnknownPtr<IConnectionPoint>(ctrl).getInterface();
            if (inst.compConn && inst.ctrlConn)
            {
                static ConnectionProxy* toCtrl = nullptr;
                static ConnectionProxy* toComp = nullptr;
                toCtrl = new ConnectionProxy(inst.ctrlConn);
                toComp = new ConnectionProxy(inst.compConn);
                inst.compConn->connect(toCtrl);
                inst.ctrlConn->connect(toComp);
            }

            // component 状态 → controller（使二者参数视图一致）
            MemStream state;
            if (comp->getState(&state) == kResultOk)
            {
                state.rewind();
                ctrl->setComponentState(&state);
            }
        }

        inst.component = comp;
        inst.processor = proc;
        inst.controller = ctrl;
        inst.className = ci.name;
        return true;
    }

    std::fprintf(stderr, "[vst3_render] no usable kVstAudioEffectClass found\n");
    return false;
}

// -----------------------------------------------------------------------------
// probe：打印全部参数元数据 JSON
// -----------------------------------------------------------------------------
int runProbe(PluginInstance& inst)
{
    if (inst.controller == nullptr)
    {
        std::fprintf(stderr, "[vst3_render] no IEditController — cannot probe\n");
        return 3;
    }

    std::printf("{\n  \"class\": \"%s\",\n", jsonEscape(inst.className).c_str());
    std::printf("  \"parameters\": [\n");

    const int32 n = inst.controller->getParameterCount();
    for (int32 i = 0; i < n; ++i)
    {
        ParameterInfo pi {};
        if (inst.controller->getParameterInfo(i, pi) != kResultOk) continue;

        const ParamValue def = pi.defaultNormalizedValue;
        String128 disp {};
        inst.controller->getParamStringByValue(pi.id, def, disp);

        std::printf("    {\"index\": %d, \"id\": %u, \"title\": \"%s\", \"shortTitle\": \"%s\", "
                    "\"units\": \"%s\", \"stepCount\": %d, \"defaultNormalized\": %.9g, "
                    "\"unitId\": %d, \"flags\": %d, \"defaultDisplay\": \"%s\"}%s\n",
                    i,
                    static_cast<unsigned>(pi.id),
                    jsonEscape(fromString128(pi.title)).c_str(),
                    jsonEscape(fromString128(pi.shortTitle)).c_str(),
                    jsonEscape(fromString128(pi.units)).c_str(),
                    pi.stepCount,
                    def,
                    pi.unitId,
                    pi.flags,
                    jsonEscape(fromString128(disp)).c_str(),
                    (i + 1 < n) ? "," : "");
    }
    std::printf("  ]\n}\n");
    return 0;
}

// -----------------------------------------------------------------------------
// sweep：扫描一个参数的显示串（用于反推真实数值范围与档位）
// -----------------------------------------------------------------------------
int runSweep(PluginInstance& inst, ParamID pid, int steps)
{
    if (inst.controller == nullptr) return 3;
    if (steps < 2) steps = 2;

    std::printf("{\n  \"id\": %u,\n  \"points\": [\n", static_cast<unsigned>(pid));
    for (int i = 0; i < steps; ++i)
    {
        const ParamValue v = static_cast<ParamValue>(i) / static_cast<ParamValue>(steps - 1);
        String128 disp {};
        inst.controller->getParamStringByValue(pid, v, disp);
        const ParamValue plain = inst.controller->normalizedParamToPlain(pid, v);
        std::printf("    {\"norm\": %.9g, \"plain\": %.9g, \"display\": \"%s\"}%s\n",
                    v, plain, jsonEscape(fromString128(disp)).c_str(),
                    (i + 1 < steps) ? "," : "");
    }
    std::printf("  ]\n}\n");
    return 0;
}

// -----------------------------------------------------------------------------
// render：stdin f32 interleaved → stdout f32 interleaved
// -----------------------------------------------------------------------------
int runRender(PluginInstance& inst,
              double sr,
              int block,
              int nch,
              const std::map<ParamID, ParamValue>& params,
              int tailSamples)
{
    // ---- 总线激活：主输入/输出各一条，按 nch 选 mono/stereo ----
    const SpeakerArrangement arr = (nch == 1) ? SpeakerArr::kMono : SpeakerArr::kStereo;
    inst.processor->setBusArrangements(const_cast<SpeakerArrangement*>(&arr), 1,
                                       const_cast<SpeakerArrangement*>(&arr), 1);

    const int32 numIn = inst.component->getBusCount(kAudio, kInput);
    const int32 numOut = inst.component->getBusCount(kAudio, kOutput);
    for (int32 i = 0; i < numIn; ++i)  inst.component->activateBus(kAudio, kInput, i, i == 0);
    for (int32 i = 0; i < numOut; ++i) inst.component->activateBus(kAudio, kOutput, i, i == 0);
    for (int32 i = 0; i < inst.component->getBusCount(kEvent, kInput); ++i)
        inst.component->activateBus(kEvent, kInput, i, false);

    ProcessSetup setup {};
    setup.processMode = kOffline;
    setup.symbolicSampleSize = kSample32;
    setup.maxSamplesPerBlock = block;
    setup.sampleRate = sr;
    if (inst.processor->setupProcessing(setup) != kResultOk)
    {
        std::fprintf(stderr, "[vst3_render] setupProcessing failed\n");
        return 4;
    }

    // ---- 参数：优先通过 controller 设定初值（很多插件在 setParamNormalized 时
    //      才把值推给 component），并同时用 IParameterChanges 在块首喂一遍 ----
    if (inst.controller != nullptr)
        for (const auto& kv : params)
            inst.controller->setParamNormalized(kv.first, kv.second);

    if (inst.component->setActive(true) != kResultOk)
    {
        std::fprintf(stderr, "[vst3_render] setActive(true) failed\n");
        return 4;
    }
    inst.processor->setProcessing(true);

    // ---- 报告插件自报的延迟补偿量 ----
    //
    // 为什么要报：参考插件的**干路**比湿路晚 51 样点（48 kHz），而这 51 在
    // global_bypass=1 下依然存在、在混响段也一样 ⇒ 它是整插件的固定延迟，
    // 不是延迟算法的一部分。宿主若做延迟补偿（DAW 都做），用户听到的干湿是
    // 对齐的，我们的候选就**不该**加这 51；若插件不自报，那 51 就是听得见
    // 的真实错位，必须复刻。这条读数决定往哪边走，所以打到 stderr 上。
    std::fprintf(stderr, "[vst3_render] latencySamples=%d\n",
                 (int) inst.processor->getLatencySamples());

    // ---- 读入全部输入 ----
    std::vector<float> interleaved;
    {
        std::vector<float> buf(4096);
        size_t r;
        while ((r = std::fread(buf.data(), sizeof(float), buf.size(), stdin)) > 0)
            interleaved.insert(interleaved.end(), buf.begin(), buf.begin() + static_cast<long>(r));
    }
    const size_t frames = (nch > 0) ? interleaved.size() / static_cast<size_t>(nch) : 0;
    const size_t total = frames + static_cast<size_t>(std::max(0, tailSamples));

    std::vector<std::vector<float>> in(static_cast<size_t>(nch), std::vector<float>(total, 0.0f));
    for (size_t f = 0; f < frames; ++f)
        for (int c = 0; c < nch; ++c)
            in[static_cast<size_t>(c)][f] = interleaved[f * static_cast<size_t>(nch) + static_cast<size_t>(c)];

    std::vector<std::vector<float>> out(static_cast<size_t>(nch), std::vector<float>(total, 0.0f));

    ParameterChanges changes;
    changes.setValues(params);

    ProcessContext ctx {};
    ctx.state = ProcessContext::kPlaying | ProcessContext::kTempoValid | ProcessContext::kTimeSigValid;
    ctx.sampleRate = sr;
    ctx.tempo = 120.0;
    ctx.timeSigNumerator = 4;
    ctx.timeSigDenominator = 4;

    std::vector<float*> inPtrs(static_cast<size_t>(nch));
    std::vector<float*> outPtrs(static_cast<size_t>(nch));

    for (size_t pos = 0; pos < total; pos += static_cast<size_t>(block))
    {
        const int32 n = static_cast<int32>(std::min(static_cast<size_t>(block), total - pos));
        for (int c = 0; c < nch; ++c)
        {
            inPtrs[static_cast<size_t>(c)] = in[static_cast<size_t>(c)].data() + pos;
            outPtrs[static_cast<size_t>(c)] = out[static_cast<size_t>(c)].data() + pos;
        }

        AudioBusBuffers inBus {};
        inBus.numChannels = nch;
        inBus.silenceFlags = 0;
        inBus.channelBuffers32 = inPtrs.data();

        AudioBusBuffers outBus {};
        outBus.numChannels = nch;
        outBus.silenceFlags = 0;
        outBus.channelBuffers32 = outPtrs.data();

        ProcessData data {};
        data.processMode = kOffline;
        data.symbolicSampleSize = kSample32;
        data.numSamples = n;
        data.numInputs = 1;
        data.numOutputs = 1;
        data.inputs = &inBus;
        data.outputs = &outBus;
        // 参数阶跃喂**前 kParamPrimeBlocks 块**，其后撤掉。
        //
        // 关于这段的来历，先把一条**已被推翻的猜测**记在这里，免得后人重走：
        // 当初 Delay Time L/R 看起来完全无效（六个 norm 全部落在 24021 样点
        // = 1/4 音符 @120 BPM），我猜是「块内 ParamID 顺序依赖」—— 队列按
        // ParamID 升序铺（std::map），Delay Time（719224438）排在 Delay Sync
        // （1678364350）之前，于是插件可能拿还没翻转的 sync 值重算延迟样点数。
        // 于是有了这里的多块喂参。**但改完重新构建、重新渲染，结果一模一样，
        // 仍是 24021** —— 猜测被自己的实验否掉了。
        //
        // 真正的原因后来由 sync / note_ms / tap 的 2×2×2 开关网格测出：ms 路径
        // 的唯一闸门是 **Delay Note/ms Display = 1.0**，与块内顺序无关（详见
        // vst3_ref.py 的 ISOLATE_DELAY 注释与 REFERENCE §14）。
        //
        // 那为什么保留多块喂参？因为它**本身是对的**，只是当时治错了病：一次性
        // 只喂第一块，依赖插件在单块内消化全部 2177 个参数阶跃；多喂几块给足
        // 内部平滑器与重算的时间，是更稳的写法。代价是最靠前几块里参数被重复
        // 设置 —— 对**离线渲染且激励放在 2.0 s**的用法没有影响（激励远在其后）。
        static constexpr size_t kParamPrimeBlocks = 8;
        const bool prime = (pos / static_cast<size_t>(block)) < kParamPrimeBlocks;
        data.inputParameterChanges = prime ? static_cast<IParameterChanges*>(&changes) : nullptr;
        data.outputParameterChanges = nullptr;
        data.processContext = &ctx;

        if (inst.processor->process(data) != kResultOk)
        {
            std::fprintf(stderr, "[vst3_render] process() failed at frame %zu\n", pos);
            break;
        }

        ctx.projectTimeSamples += n;
        ctx.continousTimeSamples += n;
        ctx.projectTimeMusic += static_cast<double>(n) / sr * (ctx.tempo / 60.0);
    }

    inst.processor->setProcessing(false);
    inst.component->setActive(false);

    std::vector<float> outInter(total * static_cast<size_t>(nch));
    for (size_t f = 0; f < total; ++f)
        for (int c = 0; c < nch; ++c)
            outInter[f * static_cast<size_t>(nch) + static_cast<size_t>(c)] = out[static_cast<size_t>(c)][f];
    std::fwrite(outInter.data(), sizeof(float), outInter.size(), stdout);
    std::fflush(stdout);
    return 0;
}

} // namespace

int main(int argc, char** argv)
{
    std::string bundle;
    double sr = 48000.0;
    int block = 512;
    int nch = 2;
    int tail = 0;
    bool probe = false;
    bool sweep = false;
    ParamID sweepId = 0;
    int sweepSteps = 21;
    std::map<ParamID, ParamValue> params;

    for (int i = 1; i < argc; ++i)
    {
        const std::string a = argv[i];
        auto next = [&](const char* what) -> std::string {
            if (i + 1 >= argc) { std::fprintf(stderr, "[vst3_render] %s needs a value\n", what); std::exit(2); }
            return argv[++i];
        };
        if (a == "--plugin")      bundle = next("--plugin");
        else if (a == "--sr")     sr = std::atof(next("--sr").c_str());
        else if (a == "--block")  block = std::atoi(next("--block").c_str());
        else if (a == "--nch")    nch = std::atoi(next("--nch").c_str());
        else if (a == "--tail")   tail = std::atoi(next("--tail").c_str());
        else if (a == "--probe")  probe = true;
        else if (a == "--sweep")  { sweep = true; sweepId = static_cast<ParamID>(std::strtoul(next("--sweep").c_str(), nullptr, 10)); }
        else if (a == "--steps")  sweepSteps = std::atoi(next("--steps").c_str());
        else if (a == "--param")
        {
            const std::string kv = next("--param");
            const auto eq = kv.find('=');
            if (eq == std::string::npos) { std::fprintf(stderr, "[vst3_render] --param needs <id>=<norm>\n"); return 2; }
            const auto id = static_cast<ParamID>(std::strtoul(kv.substr(0, eq).c_str(), nullptr, 10));
            params[id] = std::atof(kv.substr(eq + 1).c_str());
        }
        else { std::fprintf(stderr, "[vst3_render] unknown arg: %s\n", a.c_str()); return 2; }
    }

    if (bundle.empty())
    {
        std::fprintf(stderr,
            "usage:\n"
            "  vst3_render --plugin <bundle.vst3> --probe\n"
            "  vst3_render --plugin <bundle.vst3> --sweep <pid> [--steps N]\n"
            "  vst3_render --plugin <bundle.vst3> [--sr SR] [--block N] [--nch N]\n"
            "              [--tail N] [--param <pid>=<norm>]... < in.f32 > out.f32\n");
        return 2;
    }

    LoadedModule mod;
    if (!loadModule(bundle, mod)) return 3;

    PluginInstance inst;
    if (!instantiate(mod.factory, inst)) return 3;

    int rc = 0;
    if (probe)      rc = runProbe(inst);
    else if (sweep) rc = runSweep(inst, sweepId, sweepSteps);
    else            rc = runRender(inst, sr, block, nch, params, tail);

    // 有意不做完整 terminate/release：PACE 包裹的插件在卸载路径上偶有阻塞，
    // 而本工具是一次性短命进程，直接退出更稳（渲染结果已 flush）。
    std::fflush(stdout);
    std::_Exit(rc);
}
