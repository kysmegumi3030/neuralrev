/**
 * @file  ReverbCore.hpp
 * @brief 混响核心算法（不依赖 JUCE，可被离线渲染器直接编译）
 *
 * 结构依据对参考插件的黑箱实测（docs/REFERENCE.md），逐条对应：
 *
 *   §8 可分离性  → LOW/HIGH CUT 是**湿声总线后置滤波**，不在反馈环内
 *   §4 PRE-DELAY → 并联第二支路的延迟（不是串联前置延迟）
 *   §9 密度演化  → 早期稀疏、~30 ms 后致密 ⇒ 输入端有扩散级（allpass 链）
 *   §7 衰减律    → 1/T60 线性于 DECAY 参数
 *   §7 damping   → 反馈环内有**固定**低通（高频超额衰减与 DECAY 档无关，
 *                  8 kHz 处约 −10 dB/s）
 *   §3 立体声    → corr(L,R) ≈ 0.005，两声道各有抽头集合（L 477 / R 617 样点）
 *
 * 信号流：
 *
 *        ┌─ WetCore A ─────────────────────────┐
 *   in ──┤                                     ├─→ LowCut → HighCut → ×wet ─┐
 *        └─ delay(PRE-DELAY) → WetCore B ──────┘                             ├→ out
 *        └────────────────── ×dry ─────────────────────────────────────────────┘
 *
 * 每个 WetCore = 输入扩散（4 级 allpass）+ 8 路 FDN（Hadamard 混合 + 环内单极点低通）。
 * A / B 两路用**不同的延迟线长度集合**，以复现「第二路内容与第一路不同」
 * （实测：第二路不是第一路的延迟拷贝，拟合残差 0.58–0.77）。
 */

#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <vector>

#include "ReverbTuning.h"

namespace nrev
{

// ============================================================
// DelayLine —— 定长整数延迟线（读写指针，无插值）
// ------------------------------------------------------------
// 混响的延迟线长度在运行期不变（只有 PRE-DELAY 那条会变），
// 故这里用最简单的环形缓冲；PRE-DELAY 用下面的 VariableDelay。
// ============================================================
class DelayLine
{
public:
    void setSize(int samples)
    {
        size_ = std::max(1, samples);
        buf_.assign(static_cast<size_t>(size_), 0.0f);
        pos_ = 0;
    }

    void reset() { std::fill(buf_.begin(), buf_.end(), 0.0f); pos_ = 0; }

    /// 写入新样本并返回延迟 size_ 个样本的旧样本（一步完成，省一次取模）
    inline float process(float x) noexcept
    {
        const float y = buf_[static_cast<size_t>(pos_)];
        buf_[static_cast<size_t>(pos_)] = x;
        if (++pos_ >= size_) pos_ = 0;
        return y;
    }

    int size() const noexcept { return size_; }

private:
    std::vector<float> buf_;
    int size_ { 1 };
    int pos_ { 0 };
};

// ============================================================
// VariableDelay —— 可变长度延迟线（用于 PRE-DELAY）
// ------------------------------------------------------------
// 缓冲按最大延迟分配一次；延迟量变化时只改读指针偏移，不重分配。
// 参考插件把 DRY/WET 量化到 0.01 栅格，PRE-DELAY 同样是「档位式」跳变
// （实测无平滑爬升痕迹），故这里也用整数样点直接切换，不做插值。
// ============================================================
class VariableDelay
{
public:
    void setMaxDelay(int samples)
    {
        capacity_ = std::max(2, samples + 2);
        buf_.assign(static_cast<size_t>(capacity_), 0.0f);
        pos_ = 0;
    }

    void reset() { std::fill(buf_.begin(), buf_.end(), 0.0f); pos_ = 0; }

    void setDelay(int samples) noexcept
    {
        delay_ = std::clamp(samples, 0, capacity_ - 1);
    }

    inline float process(float x) noexcept
    {
        buf_[static_cast<size_t>(pos_)] = x;
        int rd = pos_ - delay_;
        if (rd < 0) rd += capacity_;
        const float y = buf_[static_cast<size_t>(rd)];
        if (++pos_ >= capacity_) pos_ = 0;
        return y;
    }

private:
    std::vector<float> buf_;
    int capacity_ { 2 };
    int delay_ { 0 };
    int pos_ { 0 };
};

// ============================================================
// Allpass —— Schroeder 全通（扩散级）
// ------------------------------------------------------------
// 用来把冲激「摊开」成密集响应，对应实测的密度演化
// （0–10 ms 占比 0.6% → 20–30 ms 63.8% → 100 ms 后 >90%）。
// 全通不改幅度响应，只改相位，所以不会污染已对齐的频响。
// ============================================================
class Allpass
{
public:
    void setSize(int samples) { line_.setSize(samples); }
    void setGain(float g) noexcept { g_ = g; }
    void reset() { line_.reset(); }

    inline float process(float x) noexcept
    {
        const float d = line_.peek();
        const float v = x + g_ * d;
        line_.push(v);
        return d - g_ * v;
    }

private:
    // 内部用「先读后写」的显式两步，故这里不复用 DelayLine::process
    class Line
    {
    public:
        void setSize(int n)
        {
            size_ = std::max(1, n);
            buf_.assign(static_cast<size_t>(size_), 0.0f);
            pos_ = 0;
        }
        void reset() { std::fill(buf_.begin(), buf_.end(), 0.0f); pos_ = 0; }
        inline float peek() const noexcept { return buf_[static_cast<size_t>(pos_)]; }
        inline void push(float v) noexcept
        {
            buf_[static_cast<size_t>(pos_)] = v;
            if (++pos_ >= size_) pos_ = 0;
        }

    private:
        std::vector<float> buf_;
        int size_ { 1 };
        int pos_ { 0 };
    };

    Line line_;
    float g_ { 0.5f };
};

// ============================================================
// OnePoleLP —— 一阶低通（反馈环内的固定 damping）
// ------------------------------------------------------------
// 对应实测：高频超额衰减与 DECAY 档**无关**（绝对 dB/s 一致），
// 说明它是每圈施加的固定滤波器，而非随参数变化的用户滤波。
// ============================================================
class OnePoleLP
{
public:
    /// fc 为 −3 dB 点（Hz）
    void setCutoff(double fc, double sampleRate) noexcept
    {
        const double x = std::exp(-2.0 * M_PI * std::max(1.0, fc) / sampleRate);
        a_ = static_cast<float>(1.0 - x);
        b_ = static_cast<float>(x);
    }
    void reset() noexcept { z_ = 0.0f; }

    inline float process(float x) noexcept
    {
        z_ = a_ * x + b_ * z_;
        return z_;
    }

private:
    float a_ { 1.0f }, b_ { 0.0f }, z_ { 0.0f };
};

// ============================================================
// Biquad —— 用于湿声总线上的 LOW CUT / HIGH CUT
// ------------------------------------------------------------
// 实测两者都是 2 极点（12 dB/oct），@fc 衰减约 −4…−5.4 dB
// （比 Butterworth 的 −3 dB 深，接近两级串联单极点的 −6 dB）。
// 两者的 Q **各自**标定：kFitLowCutQ = 0.6868 / kFitHighCutQ = 0.6521，
// 来源是 tools/measure/ref_cut_law.py 的全曲线最小二乘（见 REFERENCE.md §6.1）。
// 不是 kFitFilterQ —— 那个是「两者共用一个 Q」年代的遗留量，DSP 已不读它。
// ============================================================
class Biquad
{
public:
    void setHighpass(double fc, double q, double sr) noexcept
    {
        const double w = 2.0 * M_PI * std::clamp(fc, 1.0, sr * 0.49) / sr;
        const double cs = std::cos(w), sn = std::sin(w);
        const double alpha = sn / (2.0 * q);
        const double a0 = 1.0 + alpha;
        b0_ = static_cast<float>(((1.0 + cs) * 0.5) / a0);
        b1_ = static_cast<float>((-(1.0 + cs)) / a0);
        b2_ = b0_;
        a1_ = static_cast<float>((-2.0 * cs) / a0);
        a2_ = static_cast<float>((1.0 - alpha) / a0);
    }

    void setLowpass(double fc, double q, double sr) noexcept
    {
        const double w = 2.0 * M_PI * std::clamp(fc, 1.0, sr * 0.49) / sr;
        const double cs = std::cos(w), sn = std::sin(w);
        const double alpha = sn / (2.0 * q);
        const double a0 = 1.0 + alpha;
        b0_ = static_cast<float>(((1.0 - cs) * 0.5) / a0);
        b1_ = static_cast<float>((1.0 - cs) / a0);
        b2_ = b0_;
        a1_ = static_cast<float>((-2.0 * cs) / a0);
        a2_ = static_cast<float>((1.0 - alpha) / a0);
    }

    /// 低架（RBJ low-shelf）：fc 以下增益趋于 gainDb，fc 以上趋于 0 dB。
    /// 用途见 ReverbTuning.h 的 kFitTiltShelf* —— 补偿网络自身的低频偏热，
    /// **不是**对标参考的某个可调滤波器（LOW/HIGH CUT 是另外两个 Biquad）。
    void setLowShelf(double fc, double gainDb, double q, double sr) noexcept
    {
        const double A = std::pow(10.0, gainDb / 40.0);   // sqrt(线性增益)
        const double w = 2.0 * M_PI * std::clamp(fc, 1.0, sr * 0.49) / sr;
        const double cs = std::cos(w), sn = std::sin(w);
        const double alpha = sn / (2.0 * q);
        const double ap1 = A + 1.0, am1 = A - 1.0;
        const double tsa = 2.0 * std::sqrt(A) * alpha;
        const double a0 = ap1 + am1 * cs + tsa;
        b0_ = static_cast<float>((A * (ap1 - am1 * cs + tsa)) / a0);
        b1_ = static_cast<float>((2.0 * A * (am1 - ap1 * cs)) / a0);
        b2_ = static_cast<float>((A * (ap1 - am1 * cs - tsa)) / a0);
        a1_ = static_cast<float>((-2.0 * (am1 + ap1 * cs)) / a0);
        a2_ = static_cast<float>((ap1 + am1 * cs - tsa) / a0);
    }

    void reset() noexcept { z1_ = z2_ = 0.0f; }

    inline float process(float x) noexcept
    {
        // 转置直接 II 型：数值上比直接 I 型更稳，且只需两个状态
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
// ModulatedDelay —— 被 LFO 缓慢调制的延迟线（后期网络专用）
// ------------------------------------------------------------
// 为什么需要它：参考混响实测为**线性时变**（docs/REFERENCE.md §10）：
//   * 同一冲激挪 1 ms，响应就变（nrmse 9.2%），挪 ≥10 ms 饱和到 82%；
//   * 但 0–40 ms 区间**完全时不变**（nrmse ≤0.03%），40 ms 起突然开启并随时间增长；
//   * 第一个早期反射簇的质心在激励位移下**完全不动**（摆动 0.0 µs）。
// ⇒ 早期反射与输入扩散不调制，**只有循环网络内的延迟线被调制**，
//    每绕一圈累积一点相位偏差，故误差随时间增长。
//   1 kHz 稳态正弦的边带最小间隔 1.71 Hz，主要边带在 ±1.7/±2.5/±3.9 Hz
//   ⇒ LFO 在 1.7–4 Hz 量级；nrmse 单调饱和、无周期性回落
//   ⇒ 多条线各用**不同频率/相位**的 LFO（若共用一个，会在周期处回落）。
//
// 实现：整数基长 + 分数偏移，读指针用 **3 阶 Lagrange（4 点）** 插值。
//
// 为什么不能用线性插值（这是实测抓出来的错，别改回去）：
// 线性插值本身就是一个低通，分数部分 frac 时幅度响应为 |(1−f) + f·e^{−jω}|，
// 在 8 kHz @48 kHz 最差（f=0.5）**−1.25 dB**、frac 上均布时均值 **−0.80 dB**。
// 它串在**反馈环内**，每绕一圈吃一次，而 8 kHz 每秒绕约 17 圈
//（线长约 2800 样点）⇒ 均值口径约 **−14 dB/s**（最差 −21 dB/s）的额外
// 高频衰减 —— 比实测需要的高频超额衰减总量（约 −10 dB/s，REFERENCE §5）
// 还大。也就是说光靠环内 damping 低通根本调不出正确的高频尾巴：
// tools/fit/fit_damping_t60.py 第一轮把 kFitDampingHz 一路推到 25.2 kHz
// （**超过 Nyquist**，等于把 damping 关掉），8 kHz 的 T60 仍差 −13.3%，
// 剩下的就是这里的插值损耗。
//
// 早先那句「调制量只有零点几个样点，线性插值失真可忽略」是错的：
// kFitLfoDepthSamples = 11，frac 会**扫遍** 0…1，不是停在小偏移上。
//
// 3 阶 Lagrange 在 frac=0.5（最差点）的系数是 [−1/16, 9/16, 9/16, −1/16]，
// 8 kHz 处 |H| = 0.974 ⇒ **−0.23 dB**（frac 均值 −0.14 dB）。
// 折成每秒是均值约 **−2.5 dB/s**，比线性好 **5.6 倍**，
// 把高频损耗压回 damping 能覆盖的量级。
// 代价是每线每样点多 2 次乘加，实测 CPU 增量可忽略。
// ============================================================
class ModulatedDelay
{
public:
    /// 插值阶数与节点数。节点相对基准延迟的偏移是 −kHalf … +(kOrder−kHalf)。
    static constexpr int kOrder = tuning::kArchFracInterpOrder;
    static constexpr int kNodes = kOrder + 1;
    static constexpr int kHalf  = kOrder / 2;

    void setSize(int samples, int headroom)
    {
        // 最短延迟必须 > kHalf：负方向的节点读的是「比 di 更新」的样点
        // （回退量 di−kHalf），若 di ≤ kHalf 会回退到未来 ⇒ 读到上一轮的残值。
        base_ = std::max(kHalf + 1, samples);
        // 正方向节点最多回退 base_ + depth + (kOrder − kHalf)，故余量要够。
        capacity_ = base_ + headroom + kNodes;
        buf_.assign(static_cast<size_t>(capacity_), 0.0f);
        pos_ = 0;
    }

    void reset()
    {
        std::fill(buf_.begin(), buf_.end(), 0.0f);
        pos_ = 0;
        phase_ = 0.0f;
    }

    /// 基准长度（样点，已按采样率缩放）。逐线反馈增益要按它折算，
    /// 见 WetCore::setDecay。LFO 只在此基础上做 ±depth 的摆动，
    /// 长期平均仍是 base_，故用它算每秒圈数是对的。
    int size() const noexcept { return base_; }

    /// rateHz：LFO 频率；depth：调制深度（样点，峰值）；phase01：初相（0..1）
    void setLfo(double rateHz, double depthSamples, double phase01, double sr) noexcept
    {
        inc_ = static_cast<float>(rateHz / sr);
        depth_ = static_cast<float>(depthSamples);
        phase_ = static_cast<float>(phase01);
    }

    inline float process(float x) noexcept
    {
        buf_[static_cast<size_t>(pos_)] = x;

        // 三角波 LFO：比正弦省一次 sin 调用，且频谱上同样是窄带调制。
        // （参考插件的具体波形黑箱不可判定——边带结构对三角/正弦差异极小。）
        phase_ += inc_;
        if (phase_ >= 1.0f) phase_ -= 1.0f;
        const float tri = 4.0f * std::fabs(phase_ - 0.5f) - 1.0f;   // −1..+1
        const float d = static_cast<float>(base_) + depth_ * tri;

        // kArchFracInterpOrder 阶 Lagrange 插值读取（当前 9 阶 / 10 点）。
        // 节点是延迟 di+k，k 从 −kHalf 到 +(kOrder−kHalf)，在 t = frac 处求值。
        // 为什么不用 3 阶：它在 19 kHz 每圈仍吃 −3.39 dB，而顶端带实测只允许
        // 约 −1.2 dB 的超额衰减，见 ReverbTuning.h kArchFracInterpOrder 的推导。
        // 下限取 kHalf+1：见 setSize 的说明，di 不能小到让负向节点越过写指针。
        const int di = std::max(kHalf + 1, static_cast<int>(d));
        const float t = d - static_cast<float>(di);

        // 节点权重：Lagrange 基函数 c[i] = Π_{j≠i} (t − n_j)/(n_i − n_j)。
        // 节点位置固定为整数，故分母是编译期可折叠的常量序列；
        // 峰值 |H| 恒为 1，环内无增益尖峰（见 ReverbTuning.h 的稳定性说明）。
        float c[kNodes];
        for (int i = 0; i < kNodes; ++i)
        {
            const float ni = static_cast<float>(i - kHalf);
            float w = 1.0f;
            for (int j = 0; j < kNodes; ++j)
            {
                if (j == i) continue;
                const float nj = static_cast<float>(j - kHalf);
                w *= (t - nj) / (ni - nj);
            }
            c[i] = w;
        }

        // 节点 n = i − kHalf 对应延迟 di + n，即写指针回退 (di + n)。
        // 回退量越小的节点在缓冲里越「新」，索引 = pos_ − (di + n)。
        float y = 0.0f;
        for (int i = 0; i < kNodes; ++i)
        {
            int idx = pos_ - (di + i - kHalf);
            while (idx < 0)          idx += capacity_;
            while (idx >= capacity_) idx -= capacity_;
            y += c[i] * buf_[static_cast<size_t>(idx)];
        }

        if (++pos_ >= capacity_) pos_ = 0;
        return y;
    }

private:
    std::vector<float> buf_;
    int base_ { 1 }, capacity_ { 8 }, pos_ { 0 };
    float phase_ { 0.0f }, inc_ { 0.0f }, depth_ { 0.0f };
};

// ============================================================
// WetCore —— 单路湿声生成器（扩散 + 8 路 FDN）
// ------------------------------------------------------------
// 8 路的正交混合用 Hadamard 矩阵（1/√8 归一，无损），
// 每路环内串一个固定 damping 低通 + 统一反馈增益 g。
// 左右输出各取一组不同的抽头，复现实测的去相关
// （corr(L,R) ≈ 0.005）与不同起点（L 477 / R 617 样点）。
// ============================================================
class WetCore
{
public:
    static constexpr int kLines = tuning::kArchFdnLines;      // 8
    static constexpr int kDiffusers = tuning::kArchDiffusers; // 4

    /// lineLen / diffLen 以 48 kHz 为基准的样点数，内部按采样率缩放
    void prepare(double sampleRate,
                 const std::array<int, kLines>& lineLen48,
                 const std::array<int, kDiffusers>& diffLen48,
                 int preTap48)
    {
        sr_ = sampleRate;
        const double k = sampleRate / tuning::kRefSampleRate;

        const double depth = tuning::kFitLfoDepthSamples * k;
        for (int i = 0; i < kLines; ++i)
        {
            const size_t si = static_cast<size_t>(i);
            lines_[si].setSize(
                std::max(1, static_cast<int>(std::lround(lineLen48[si] * k))),
                static_cast<int>(std::ceil(depth)) + tuning::kArchFracNodeSpan);
            // 每条线一个**不同频率、不同初相**的 LFO：实测 nrmse 随激励位移
            // 单调饱和、无周期性回落 ⇒ 不是单一共用 LFO（见 §10）。
            lines_[si].setLfo(tuning::kFitLfoRatesHz[si], depth,
                              tuning::kFitLfoPhases[si], sampleRate);
            damp_[si].setCutoff(tuning::kFitDampingHz, sampleRate);
        }
        for (int i = 0; i < kDiffusers; ++i)
        {
            diff_[static_cast<size_t>(i)].setSize(
                std::max(1, static_cast<int>(std::lround(diffLen48[static_cast<size_t>(i)] * k))));
            diff_[static_cast<size_t>(i)].setGain(tuning::kArchDiffuserGain);
        }
        preTap_.setMaxDelay(std::max(1, static_cast<int>(std::lround(preTap48 * k)) + 1));
        preTap_.setDelay(static_cast<int>(std::lround(preTap48 * k)));
        reset();
    }

    void reset()
    {
        for (auto& l : lines_) l.reset();
        for (auto& d : damp_) d.reset();
        for (auto& a : diff_) a.reset();
        preTap_.reset();
        state_.fill(0.0f);
    }

    /// 设定衰减：按 T60 给出**逐线**反馈增益。
    ///
    /// 为什么不能所有线共用一个 g（这是实测暴露出来的 bug）：
    /// 第 i 条线每秒绕 sr/L_i 圈，线长 1697…4033 差 2.4 倍，
    /// 共用 g 时每条线的**每秒**衰减率就差 2.4 倍 —— 尾巴变成 8 个不同
    /// 衰减率的混合，而不是单一的目标 T60。短线先衰完，剩下长线拖尾，
    /// 整体包络不再是直线，回归出来的 T60 也就对不上任何一条线。
    ///
    /// 正确做法（FDN 标准）：让**每条线**各自在 T60 内累计 −60 dB，
    ///     g_i = 10^(−3·L_i / (T60·sr))
    /// 这样 8 条线衰减率一致，整网包络是单一指数，T60 才有定义。
    ///
    /// 环内的 damping 低通与插值读取还有额外损耗，故按 kFitT60BudgetScale
    /// 放大目标后再编预算（实测为**比例**关系，不是固定损耗，推导见该常数注释）。
    ///
    /// `scaleMul` 是**逐档**修正（tuning::t60BudgetScaleFromNorm 的返回值）。
    /// 由调用方传入而不是在这里查表：本类只拿到 T60 秒数，而修正表是按
    /// DECAY 归一值定义的，两者之间是非线性的幂律（t60FromDecaySec），
    /// 在这里反解会引入不必要的误差和一份重复的律。默认 1.0 ⇒ 与单常数等价。
    void setDecay(double t60Sec, double sampleRate, double scaleMul = 1.0) noexcept
    {
        const double budget = t60Sec * tuning::kFitT60BudgetScale * scaleMul;
        for (int i = 0; i < kLines; ++i)
        {
            const size_t si = static_cast<size_t>(i);
            const double len = static_cast<double>(lines_[si].size());
            if (budget <= 0.0 || len <= 0.0) { g_[si] = 0.0f; continue; }
            const double perRoundDb = -60.0 * len / (budget * sampleRate);
            g_[si] = static_cast<float>(
                std::clamp(std::pow(10.0, perRoundDb / 20.0), 0.0, 0.9999));
        }
    }

    /// 处理一个样本，输出 (L, R) 两路湿声
    inline void process(float x, float& outL, float& outR) noexcept
    {
        // ---- 入口固定抽头：复现湿声起点（实测 L 477 样点）----
        float v = preTap_.process(x);

        // ---- 输入扩散：把冲激摊成密集响应 ----
        for (auto& a : diff_) v = a.process(v);

        // ---- FDN：读延迟线 → Hadamard 混合 → damping → 反馈写回 ----
        std::array<float, kLines> d {};
        for (int i = 0; i < kLines; ++i)
            d[static_cast<size_t>(i)] = state_[static_cast<size_t>(i)];

        std::array<float, kLines> m {};
        hadamard8(d, m);

        for (int i = 0; i < kLines; ++i)
        {
            const float fb = damp_[static_cast<size_t>(i)].process(m[static_cast<size_t>(i)])
                           * g_[static_cast<size_t>(i)];
            state_[static_cast<size_t>(i)] = lines_[static_cast<size_t>(i)].process(v + fb);
        }

        // ---- 输出抽头：L 取偶路、R 取奇路并交替反相 ----
        // 交替反相是让两声道去相关的最省算力手段（实测目标 corr≈0.005），
        // 且不改变各自的幅度谱。
        float l = 0.0f, r = 0.0f;
        for (int i = 0; i < kLines; i += 2)
        {
            l += d[static_cast<size_t>(i)];
            r += (i % 4 == 0) ? d[static_cast<size_t>(i + 1)] : -d[static_cast<size_t>(i + 1)];
        }
        constexpr float norm = 0.5f;  // 4 路求和的粗归一
        outL = l * norm;
        outR = r * norm;
    }

private:
    /// 8 点 Hadamard 变换（快速蝶形，1/√8 归一 → 正交、无损）
    static inline void hadamard8(const std::array<float, kLines>& in,
                                 std::array<float, kLines>& out) noexcept
    {
        float a0 = in[0] + in[1], a1 = in[0] - in[1];
        float a2 = in[2] + in[3], a3 = in[2] - in[3];
        float a4 = in[4] + in[5], a5 = in[4] - in[5];
        float a6 = in[6] + in[7], a7 = in[6] - in[7];

        float b0 = a0 + a2, b2 = a0 - a2;
        float b1 = a1 + a3, b3 = a1 - a3;
        float b4 = a4 + a6, b6 = a4 - a6;
        float b5 = a5 + a7, b7 = a5 - a7;

        constexpr float s = 0.35355339059327373f;  // 1/√8
        out[0] = (b0 + b4) * s;
        out[1] = (b1 + b5) * s;
        out[2] = (b2 + b6) * s;
        out[3] = (b3 + b7) * s;
        out[4] = (b0 - b4) * s;
        out[5] = (b1 - b5) * s;
        out[6] = (b2 - b6) * s;
        out[7] = (b3 - b7) * s;
    }

    double sr_ { tuning::kRefSampleRate };
    // 后期网络的延迟线是**被调制**的（早期扩散与入口抽头不调制，见 §10）
    std::array<ModulatedDelay, kLines> lines_ {};
    std::array<OnePoleLP, kLines> damp_ {};
    std::array<Allpass, kDiffusers> diff_ {};
    std::array<float, kLines> state_ {};
    VariableDelay preTap_;
    /// 逐线反馈增益（按各自线长换算，见 setDecay 的推导）
    std::array<float, kLines> g_ { };
};

} // namespace nrev
