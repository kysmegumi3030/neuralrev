/**
 * @file  DelayCore.hpp
 * @brief 延迟段的 DSP 基元：LFO 调制写指针的延迟线、环内滤波器、静态饱和
 *
 * 结构与各常数的实测依据见 docs/REFERENCE.md §14 与 DelayTuning.h 的逐条注释。
 * 与 ReverbCore.hpp 同一套风格：每个类只做一件事，构造即可用，
 * 参数换算全部在 DelayTuning.h 里，本文件只实现机制。
 */

#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <vector>

#include "DelayTuning.h"

namespace nrev
{

// ============================================================
// LfoDelayLine —— LFO 调制**写指针**的延迟线
// ------------------------------------------------------------
// 为什么调制写指针而不是读指针：这是实测定下来的机制，判据是深度随延迟
// 时长的律 depth(D) = 2A·|sin(π·D/T)|，以及 D 恰好等于一个 LFO 周期时的
// **真零点**（norm=0.65，深度 0.0122 样点，比邻档小两个数量级，且穿越时
// 初相跳 178.2°）。详见 DelayTuning.h 的 kMeasLfoAmpSamples。
//
// 机制上等价的说法：延迟量 = 读出时刻的 LFO 相位 − 写入时刻的 LFO 相位。
// 本实现直接照抄机制 —— **不**去实现那条 depth 闭式解。这样做的好处是
// 非单调性、零点、初相随 D 的漂移全部自动涌现，不需要任何查表；
// 而按闭式解实现只能对上幅度，对不上相位。
//
// 实现方式：写指针按 base + A·sin(2πft) 摆动（读指针恒定步进）。
// 读出时刻的净调制自然就是两个时刻 LFO 之差。
//
// 内插用 kArchFracInterpOrder 阶 Lagrange —— 与混响同一个理由：
// 写指针摆幅 ±3.28 样点，frac 会扫遍 0…1，低阶内插的高频损耗不可忽略
// （线性插值在 8 kHz 每圈约 −0.5 dB，反馈 0.8 时几十圈就是几十 dB）。
// ============================================================
class LfoDelayLine
{
public:
    static constexpr int kOrder = delaytuning::kArchFracInterpOrder;
    static constexpr int kNodes = kOrder + 1;
    static constexpr int kHalf  = kOrder / 2;

    /// maxDelay：最大基准延迟（样点）；headroom：LFO 摆幅余量
    void prepare(int maxDelay, int headroom)
    {
        capacity_ = std::max(16, maxDelay + headroom + kNodes + 4);
        buf_.assign(static_cast<size_t>(capacity_), 0.0f);
        // lfo_ 与 buf_ 必须同容量：process() 用同一个 readPos_ 索引两者，
        // 靠它回看「写入那一刻的 LFO 值」。
        lfo_.assign(static_cast<size_t>(capacity_), 0.0f);
        reset();
    }

    void reset()
    {
        std::fill(buf_.begin(), buf_.end(), 0.0f);
        std::fill(lfo_.begin(), lfo_.end(), 0.0f);
        readPos_ = 0;
        phase_ = 0.0;
    }

    /// 基准延迟（样点，可为分数）
    void setDelay(double samples) noexcept { base_ = samples; }

    /// LFO：速率（Hz）、幅度（样点，峰值）
    void setLfo(double rateHz, double ampSamples, double sr) noexcept
    {
        inc_ = rateHz / sr;
        amp_ = ampSamples;
    }

    /// 相位归零。LFO 实测**锚定在渲染起点的绝对时间**（重复渲染 Δ=0，
    /// 渲染长度改变 Δ=0，激励提前整周期则深度重合到 0.0181 样点），
    /// 所以复位时必须把相位也归零，否则对拍时相位对不上。
    void resetPhase() noexcept { phase_ = 0.0; }

    /// 设定起相（周期的分数，0…1）。
    ///
    /// 为什么需要它：LFO 锚定在渲染起点，但参考侧的**起相**未知（只知道它
    /// 确定）。对拍时若不标定这个相位差，等价于随机取一个 —— 实测参考与
    /// 自己比、激励只挪 480 样点就给出 8.57 dB，足以把一个正确的实现判成失败。
    ///
    /// 这是**一个全局标量**，不随频率/延迟档/反馈变化。用显式相位而不是
    /// 「把激励整体平移」来标定：后者在猝发靠近缓冲边界时会把回声移出窗外，
    /// 前者不动信号，只动 LFO。
    void setPhase(double frac) noexcept
    {
        phase_ = frac - std::floor(frac);
    }

    inline float process(float x) noexcept
    {
        // 写指针被 LFO 调制：写入位置 = 名义位置 + A·sin(2πft)。
        // 用「把样点写到分数位置」的对偶做法：保持整数写、把调制量搬到
        // 读出侧的**基准**上，并让它取写入时刻的相位 —— 二者等价，
        // 但整数写不会污染缓冲（分数写需要散布到相邻格，会引入额外滤波）。
        const double lfoNow = amp_ * std::sin(2.0 * M_PI * phase_);
        phase_ += inc_;
        if (phase_ >= 1.0) phase_ -= 1.0;

        buf_[static_cast<size_t>(readPos_)] = x;
        lfo_[static_cast<size_t>(readPos_)] = static_cast<float>(lfoNow);

        // 读出位置 = 写指针 − 基准延迟；再补上「写入那一刻的 LFO 偏移」。
        // 写入时刻 ≈ 当前时刻 − base，故取那一格记下的 LFO 值。
        const int backInt = static_cast<int>(base_);
        int wIdx = readPos_ - backInt;
        while (wIdx < 0) wIdx += capacity_;
        const double lfoThen = static_cast<double>(lfo_[static_cast<size_t>(wIdx)]);

        // 净延迟：基准 + （写入时刻 LFO − 当前时刻 LFO）。
        // 这一项的幅度正是 2A|sin(πD/T)| —— 实测的那条律，
        // 但这里是**推出来的**，不是填进去的。
        const double d = base_ + (lfoThen - lfoNow);

        const double dc = std::clamp(d, static_cast<double>(kHalf + 1),
                                     static_cast<double>(capacity_ - kNodes - 2));
        const int di = static_cast<int>(dc);
        const float t = static_cast<float>(dc - static_cast<double>(di));

        // Lagrange 基函数（节点为整数 ⇒ 分母是常量序列，峰值 |H| ≡ 1）
        float c[kNodes];
        for (int i = 0; i < kNodes; ++i)
        {
            const float ni = static_cast<float>(i - kHalf);
            float wt = 1.0f;
            for (int j = 0; j < kNodes; ++j)
            {
                if (j == i) continue;
                const float nj = static_cast<float>(j - kHalf);
                wt *= (t - nj) / (ni - nj);
            }
            c[i] = wt;
        }

        float y = 0.0f;
        for (int i = 0; i < kNodes; ++i)
        {
            int idx = readPos_ - (di + i - kHalf);
            while (idx < 0)          idx += capacity_;
            while (idx >= capacity_) idx -= capacity_;
            y += c[i] * buf_[static_cast<size_t>(idx)];
        }

        if (++readPos_ >= capacity_) readPos_ = 0;
        return y;
    }

private:
    std::vector<float> buf_;
    std::vector<float> lfo_;
    int capacity_ { 16 };
    int readPos_ { 0 };
    double base_ { 1.0 };
    double phase_ { 0.0 }, inc_ { 0.0 }, amp_ { 0.0 };
};

// ============================================================
// DelayBiquad —— RBJ 二阶（环内的用户 LP/HP 与固定级联）
// ------------------------------------------------------------
// 用户 LP/HP 实测都是**二阶 Butterworth 且显示 fc 就是真 −3 dB 点**
// （判据见 DelayTuning.h kMeasUserFilterOrder）；固定部分用两级二阶级联
// 拟合到 0.046 dB。两者共用这个类。
//
// 与 ReverbCore.hpp 的 Biquad 分开：那个是湿声总线上的后置滤波，
// 这里是**环内**的，系数换算相同但语义不同，混用会让两段的调参互相污染。
// ============================================================
class DelayBiquad
{
public:
    void setLowpass(double fc, double q, double sr) noexcept
    {
        const double w = 2.0 * M_PI * std::clamp(fc, 1.0, sr * 0.49) / sr;
        const double cs = std::cos(w), sn = std::sin(w);
        const double alpha = sn / (2.0 * std::max(1.0e-3, q));
        const double a0 = 1.0 + alpha;
        b0_ = static_cast<float>(((1.0 - cs) * 0.5) / a0);
        b1_ = static_cast<float>((1.0 - cs) / a0);
        b2_ = b0_;
        a1_ = static_cast<float>((-2.0 * cs) / a0);
        a2_ = static_cast<float>((1.0 - alpha) / a0);
    }

    void setHighpass(double fc, double q, double sr) noexcept
    {
        const double w = 2.0 * M_PI * std::clamp(fc, 1.0, sr * 0.49) / sr;
        const double cs = std::cos(w), sn = std::sin(w);
        const double alpha = sn / (2.0 * std::max(1.0e-3, q));
        const double a0 = 1.0 + alpha;
        b0_ = static_cast<float>(((1.0 + cs) * 0.5) / a0);
        b1_ = static_cast<float>((-(1.0 + cs)) / a0);
        b2_ = b0_;
        a1_ = static_cast<float>((-2.0 * cs) / a0);
        a2_ = static_cast<float>((1.0 - alpha) / a0);
    }

    void reset() noexcept { z1_ = z2_ = 0.0f; }

    inline float process(float x) noexcept
    {
        // 转置直接 II 型：状态少、数值稳定性好
        const float y = b0_ * x + z1_;
        z1_ = b1_ * x - a1_ * y + z2_;
        z2_ = b2_ * x - a2_ * y;
        return y;
    }

private:
    float b0_ { 1.0f }, b1_ { 0.0f }, b2_ { 0.0f }, a1_ { 0.0f }, a2_ { 0.0f };
    float z1_ { 0.0f }, z2_ { 0.0f };
};

// ============================================================
// WetSaturator —— 湿声总线上的静态奇对称饱和
// ------------------------------------------------------------
// 实测特征（tools/measure/ref_delay_sat.py，§14.4）：
//   * 只有 H3/H5，无 H2/H4 ⇒ **奇函数**；
//   * **无记忆**（静态）：谱不随频率变，不是动态压缩；
//   * 增益 amp ≤ 0.03 恒为 0.432732（线性区平台），amp=1.0 为 0.402685。
//
// 用 tanh 型：g·tanh(k·x)/k，其中 k 由「满幅增益比」定：
//   0.402685 / 0.432732 = 0.93055 = tanh(k)/k  ⇒  k ≈ 0.646
// tanh 的 H3/H1 在此 k 下约 −44 dB，与实测的 H3 量级一致。
// ============================================================
// ============================================================
// LoopFir —— 环内固定滤波器（抗镜像核），24 抽头 FIR
// ------------------------------------------------------------
// 抽头是**直接测出来的**（参考 fb=0 时的单回声就是它的冲激响应），
// 不是拟合的极点。为什么不用级联二阶：幅度上级联最好 2.97 dB
// 而 FIR 到 12 kHz 是 0.043 dB；群延迟上级联只有约 4 样点而实测是 22。
// 推导与提取步骤见 DelayTuning.h kMeasLoopFirTaps。
//
// 24 抽头的直接卷积每样点 24 次乘加。环内每声道一个，48 kHz 立体声
// 约 2.3 M MAC/s —— 比那条 15 阶 Lagrange 内插（每样点 16 次乘加加上
// 系数计算）还轻，不需要 FFT 卷积。
// ============================================================
class LoopFir
{
public:
    static constexpr int kTaps = delaytuning::kMeasLoopFirLength;

    void reset() noexcept
    {
        z_.fill(0.0f);
        pos_ = 0;
    }

    inline float process(float x) noexcept
    {
        z_[static_cast<size_t>(pos_)] = x;
        float y = 0.0f;
        int idx = pos_;
        for (int i = 0; i < kTaps; ++i)
        {
            y += static_cast<float>(delaytuning::kMeasLoopFirTaps[static_cast<size_t>(i)])
               * z_[static_cast<size_t>(idx)];
            if (--idx < 0) idx = kTaps - 1;
        }
        if (++pos_ >= kTaps) pos_ = 0;
        return y;
    }

private:
    std::array<float, kTaps> z_ {};
    int pos_ { 0 };
};

// ============================================================
// WetPreDelay —— 固定 **20.509** 样点延迟，只在**湿抽头**上过一次
// ------------------------------------------------------------
// 为什么它不在反馈环内（这一条是量出来的，不是设计选择）：
// 逐圈累积延迟的**增量**把两种拓扑区分开了
// （tools/measure/ref_delay_round_delay.py，D=4800 整数档、冲激、fb=1.0）：
//
//   拓扑 A（预延迟在环内）  ⇒ 每圈增量 = 16 + gd
//   拓扑 B（只在湿抽头一次）⇒ 每圈增量 = gd，第 1 圈额外多 16
//
//   参考实测增量 **11.37** 样点/圈（重心口径，std 2.60）
//   候选（曾是 A）实测 **25.88** ⇒ 差 14.5 ≈ 16
//   抽头自身的群延迟独立算出 gd = 6.73（DC）/ 6.96（重心）
//   参考第 1 圈 26.59 − 增量 11.37 = **15.2 ≈ 16** ✓
//
// 三个数自洽地指向 B：参考每圈只吃 gd，而那 16 样点只在输出路径上过一次。
//
// 为什么当初没看出来：`kMeasLoopPreDelaySamples` 是用 **echo1** 定的
// （参考在 D+15 及之前严格为 0），而 echo1 恰好只过一次 ——
// **两种拓扑在 echo1 上完全相同**。要区分它们必须看 echo2 以后的**增量**。
// 症状是 fb=1.0 档逐圈滞后线性累积 +15.6/圈（46.28 dB），
// 而 fb=0 档完全正常（1.19 dB ✓）—— 那个对比本身就是定位线索。
// ============================================================
// ------------------------------------------------------------
// 它是 **20.509** 样点，不是 16 —— 分数部分 4.509 由反卷积单独定出
// （见 DelayTuning.h 的 kMeasWetPreDelayFrac）。这里用与主延迟线同族的
// 中心 Lagrange 插值实现那 0.509，系数在 reset() 里算一次：τ 是常数，
// 没有理由每样点重算 16 个基函数。
//
// 为什么不能只用整数 20 或 21：缺失级的反卷积核是**两个相邻的近等抽头**
// （0.481 / 0.497），那是半样点的指纹；取整会把它变成单抽头，等于故意
// 留下 0.5 样点的宽带相位误差。
// ------------------------------------------------------------
class WetPreDelay
{
public:
    static constexpr int kOrder = delaytuning::kArchFracInterpOrder;
    static constexpr int kNodes = kOrder + 1;
    static constexpr int kHalf  = kOrder / 2;

    /// 总延迟 = 整数 16 + 湿抽头**净**分数（现为 **−5.8927**）⇒ **10.1073** 样点。
    ///
    /// 净分数为负不是笔误 —— 它是三项相减的结果，湿路径总量 4.509 被两个
    /// 别处已经承担掉的量扣干净了（见 DelayTuning.h 的 kFitWetPreDelayFracNet）：
    ///   4.509（整条湿路径，由 echo1 定）
    ///   − 0.4017（属**环内**，逐圈量，由逐圈重心回归定 ⇒ kFitLoopExtraDelay）
    ///   − 10（WetShelf 对称核的群延迟，恒定、与档位无关）
    /// 三项之和必须守恒 = 4.509，否则闭合了逐圈或搁架就会破坏 echo1。
    static constexpr double kTotal =
        static_cast<double>(delaytuning::kMeasLoopPreDelaySamples)
        + delaytuning::kFitWetPreDelayFracNet;

    /// 缓冲要装下最远的节点：floor(kTotal) + kHalf，再留几格余量。
    static constexpr int kLen = static_cast<int>(kTotal) + kHalf + 4;

    void reset() noexcept
    {
        z_.fill(0.0f);
        pos_ = 0;

        // Lagrange 基函数（节点为整数偏移 i − kHalf，分母是常量序列）
        const int di = static_cast<int>(kTotal);
        const float t = static_cast<float>(kTotal - static_cast<double>(di));
        di_ = di;
        for (int i = 0; i < kNodes; ++i)
        {
            const float ni = static_cast<float>(i - kHalf);
            float wt = 1.0f;
            for (int j = 0; j < kNodes; ++j)
            {
                if (j == i) continue;
                const float nj = static_cast<float>(j - kHalf);
                wt *= (t - nj) / (ni - nj);
            }
            c_[static_cast<size_t>(i)] = wt;
        }
    }

    inline float process(float x) noexcept
    {
        z_[static_cast<size_t>(pos_)] = x;

        float y = 0.0f;
        for (int i = 0; i < kNodes; ++i)
        {
            int idx = pos_ - (di_ + i - kHalf);
            while (idx < 0)     idx += kLen;
            while (idx >= kLen) idx -= kLen;
            y += c_[static_cast<size_t>(i)] * z_[static_cast<size_t>(idx)];
        }

        if (++pos_ >= kLen) pos_ = 0;
        return y;
    }

private:
    std::array<float, static_cast<size_t>(kLen)> z_ {};
    std::array<float, kNodes> c_ {};
    int di_ { 0 };
    int pos_ { 0 };
};

// ============================================================
// WetShelf —— 长延迟档的顶端八度修正（21 抽头零相位对称 FIR）
// ============================================================
// 依据、位置判定、形状选择与落点校核全部在 DelayTuning.h 的
// kFitWetShelf* 注释里，这里只说实现上的三件事。
//
// 1. **对称核只存半边**，卷积时两侧配对：
//        y = k[0]·z[c] + Σ_{i=1..H} k[i]·(z[c−i] + z[c+i])
//    省一半乘法，且对称性由构造保证 —— 不可能因为抽头写错而引入相位。
//
// 2. **群延迟恒为 kHalf = 10 样点，与档位无关**（对称核的性质）。
//    恒等档也是「中心抽头 = 1、其余 0」的单位冲激，**不是旁通** ——
//    所以那 10 样点在所有档位上都存在，已由 kFitWetPreDelayFracNet
//    一次性扣掉。这一点是它能被静态补偿的前提。
//
// 3. 抽头在 `setDelay()` 里按 D 插值算一次，不在 process() 里算。
//    D 只在 updateDerived() 变，没有理由每样点重算。
// ------------------------------------------------------------
class WetShelf
{
public:
    static constexpr int kHalf  = delaytuning::kFitWetShelfHalf;
    static constexpr int kTaps  = 2 * kHalf + 1;
    static constexpr int kKnots = delaytuning::kFitWetShelfKnots;
    static constexpr int kLen   = kTaps + 1;

    void reset() noexcept
    {
        z_.fill(0.0f);
        pos_ = 0;
    }

    /// 按延迟长度 D（样点）设定抽头。
    ///
    /// 三段：D ≤ 起效点恒等；起效点…第一个节点在「恒等」与该节点之间插值；
    /// 其上在相邻节点之间插值，超过最后一个节点则**不外推**（取端点值）。
    /// 不外推的理由与 §12.7 那张分段线性表相同：区间外没有可信实测，
    /// 外推会把趋势一路拉到最敏感的极限档上。
    void setDelay(double d) noexcept
    {
        // 恒等核：中心抽头 1，其余 0（不是旁通，仍有 kHalf 的群延迟）
        double h[kHalf + 1] = { 1.0 };

        const double d0 = delaytuning::kFitWetShelfOnsetSamples;
        const auto&  ks = delaytuning::kFitWetShelfKnotSamples;
        const auto&  ts = delaytuning::kFitWetShelfTaps;

        if (d > d0)
        {
            if (d <= ks[0])
            {
                // 恒等 → 第一个节点
                const double u = (d - d0) / (ks[0] - d0);
                for (int i = 0; i <= kHalf; ++i)
                {
                    const double id = (i == 0) ? 1.0 : 0.0;
                    h[i] = id + u * (ts[0][i] - id);
                }
            }
            else
            {
                int seg = kKnots - 2;
                for (int s = 0; s < kKnots - 1; ++s)
                    if (d <= ks[s + 1]) { seg = s; break; }

                double u = (d - ks[seg]) / (ks[seg + 1] - ks[seg]);
                u = std::clamp(u, 0.0, 1.0);          // 末节点之上不外推
                for (int i = 0; i <= kHalf; ++i)
                    h[i] = ts[seg][i] + u * (ts[seg + 1][i] - ts[seg][i]);
            }
        }

        for (int i = 0; i <= kHalf; ++i)
            k_[static_cast<size_t>(i)] = static_cast<float>(h[i]);
    }

    inline float process(float x) noexcept
    {
        z_[static_cast<size_t>(pos_)] = x;

        const auto at = [this](int back) noexcept -> float
        {
            int idx = pos_ - back;
            while (idx < 0)     idx += kLen;
            while (idx >= kLen) idx -= kLen;
            return z_[static_cast<size_t>(idx)];
        };

        // 中心抽头在 kHalf 样点之前；对称配对取 kHalf∓i
        float y = k_[0] * at(kHalf);
        for (int i = 1; i <= kHalf; ++i)
            y += k_[static_cast<size_t>(i)] * (at(kHalf - i) + at(kHalf + i));

        if (++pos_ >= kLen) pos_ = 0;
        return y;
    }

private:
    std::array<float, static_cast<size_t>(kLen)> z_ {};
    /// 默认恒等（中心抽头 1），不是全零 —— 万一 setDelay 还没被调用过，
    /// 全零核会让湿路径静音，那是一个很难查的静默失效。
    std::array<float, kHalf + 1> k_ { 1.0f };
    int pos_ { 0 };
};

/// 干路的**纯整数**延迟（参考自报的 51 样点延迟补偿量，见 DelayTuning.h
/// 的 kMeasDryLatencySamples48k）。
///
/// 为什么不复用 WetPreDelay：那是 15 阶 Lagrange 分数延迟，为了 4.1073 那个
/// 小数存在。这里的量是**整数**（实测干路冲激是单样点、无拖尾），用分数插值
/// 只会引入本不存在的旁瓣。一个环形缓冲 + 读指针就够，且干路上没有滤波，
/// 这样能保持「冲激进、冲激出」的机器精度。
class DryLatency
{
public:
    /// n 为整数样点数；按采样率折算后可能超过 48 kHz 的 51，留够余量。
    void prepare(int n)
    {
        n_ = std::max(0, n);
        z_.assign(static_cast<size_t>(n_) + 1, 0.0f);
        pos_ = 0;
    }

    void reset() noexcept
    {
        std::fill(z_.begin(), z_.end(), 0.0f);
        pos_ = 0;
    }

    inline float process(float x) noexcept
    {
        if (n_ <= 0) return x;
        const int len = static_cast<int>(z_.size());
        z_[static_cast<size_t>(pos_)] = x;
        int rd = pos_ - n_;
        if (rd < 0) rd += len;
        const float y = z_[static_cast<size_t>(rd)];
        if (++pos_ >= len) pos_ = 0;
        return y;
    }

private:
    std::vector<float> z_ {};
    int n_ { 0 };
    int pos_ { 0 };
};

class WetSaturator
{
public:
    void setDrive(double gain, double k) noexcept
    {
        gain_ = static_cast<float>(gain);
        k_ = static_cast<float>(std::max(1.0e-6, k));
        invK_ = 1.0f / k_;
    }

    /// g·tanh(k·x)/k —— 小信号处斜率恰为 g（因 tanh(kx)/k → x），
    /// 所以 gain_ 可以直接用实测的线性区平台值，不需要再配平。
    inline float process(float x) noexcept
    {
        return gain_ * std::tanh(k_ * x) * invK_;
    }

private:
    float gain_ { 1.0f }, k_ { 1.0f }, invK_ { 1.0f };
};

} // namespace nrev
