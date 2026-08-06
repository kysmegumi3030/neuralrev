/**
 * @file  ReverbEffect.h
 * @brief 混响效果器（JUCE 侧封装）：参数换算 + 双路 WetCore + 后置滤波 + 干湿混合
 *
 * 结构与各常数的实测依据见 docs/REFERENCE.md 与 ReverbTuning.h 的逐条注释。
 *
 * 与模板 GainEffect.h 一致的接口约定：
 *   prepare(spec) / reset() / process(context)
 * 另加 setParametersNormalized()（供离线渲染器与 A/B 脚本按归一值驱动，
 * 与参考插件的 VST3 normalized 语义一致，保证「测的就是发布的」）。
 */

#pragma once

// 离线渲染器（tools/nrev_render）在不引入 JUCE 的前提下编译本头：
// 它自带 juce::dsp::ProcessSpec 与 Block/Context 的极小替身，并定义
// NREV_NO_JUCE 来跳过这里的 JUCE 头。插件构建时不定义该宏，走正常路径。
// 这样「发布的算法」与「被拟合的算法」是同一份源码。
#ifndef NREV_NO_JUCE
#include <juce_dsp/juce_dsp.h>
#endif

#include "ReverbCore.hpp"
#include "ReverbTuning.h"

namespace nrev
{

class ReverbEffect
{
public:
    void prepare(const juce::dsp::ProcessSpec& spec)
    {
        sr_ = spec.sampleRate;

        const int preL = static_cast<int>(std::lround(
            tuning::kMeasWetOnsetSamples * sr_ / tuning::kRefSampleRate));
        const int preR = static_cast<int>(std::lround(
            tuning::kMeasWetOnsetSamplesR * sr_ / tuning::kRefSampleRate));

        // A 路用左声道起点，B 路用右声道起点：两路入口抽头不同，
        // 与「第二路内容不同于第一路」的实测一致。
        coreA_.prepare(sr_, tuning::kArchLinesA, tuning::kArchDiffusersA, preL);
        coreB_.prepare(sr_, tuning::kArchLinesB, tuning::kArchDiffusersB, preR);

        const int maxPre = static_cast<int>(std::ceil(
            tuning::kMeasPredelayMaxMs * 1.0e-3 * sr_)) + 4;
        preDelay_.setMaxDelay(maxPre);

        reset();
        applyParams();
    }

    void reset()
    {
        coreA_.reset();
        coreB_.reset();
        preDelay_.reset();
        for (auto& f : lowCut_) f.reset();
        for (auto& f : highCut_) f.reset();
        for (auto& f : tilt_) f.reset();
    }

    // ------------------------------------------------------------
    // 参数设置（归一值 0..1，与参考插件的 VST3 normalized 一致）
    // ------------------------------------------------------------
    void setParametersNormalized(float dryWet, float preDelay, float decay,
                                 float lowCut, float highCut) noexcept
    {
        nDryWet_ = dryWet;
        nPreDelay_ = preDelay;
        nDecay_ = decay;
        nLowCut_ = lowCut;
        nHighCut_ = highCut;
        applyParams();
    }

    /// 以真实工程量设置（供 UI/APVTS 直接喂真实值）
    void setParameters(float dryWet, float preDelayMs, float decaySec,
                       float lowCutHz, float highCutHz) noexcept
    {
        using namespace tuning;
        nDryWet_ = dryWet;
        nPreDelay_ = static_cast<float>(predelayNormFromMs(preDelayMs));
        nDecay_ = static_cast<float>(
            (decaySec - kMeasDecayMinSec) / (kMeasDecayMaxSec - kMeasDecayMinSec));
        nLowCut_ = static_cast<float>(
            (lowCutHz - kMeasLowCutMinHz) / (kMeasLowCutMaxHz - kMeasLowCutMinHz));
        nHighCut_ = static_cast<float>(
            (highCutHz - kMeasHighCutMinHz) / (kMeasHighCutMaxHz - kMeasHighCutMinHz));
        applyParams();
    }

    // ------------------------------------------------------------
    // 处理
    // ------------------------------------------------------------
    template <typename ProcessContext>
    void process(const ProcessContext& context) noexcept
    {
        auto& block = context.getOutputBlock();
        const auto numCh = block.getNumChannels();
        const auto numSamples = block.getNumSamples();
        if (numCh == 0 || numSamples == 0) return;

        float* chL = block.getChannelPointer(0);
        float* chR = (numCh > 1) ? block.getChannelPointer(1) : nullptr;

        for (size_t i = 0; i < numSamples; ++i)
        {
            const float inL = chL[i];
            const float inR = chR ? chR[i] : inL;
            // 混响入口是单声道求和：参考插件在 mono 输入模式下工作，
            // 且实测两声道湿声完全去相关（corr 0.005）——去相关来自网络本身
            // 的不同抽头，而不是输入的立体声差异。
            const float mono = 0.5f * (inL + inR);

            float aL = 0.0f, aR = 0.0f, bL = 0.0f, bR = 0.0f;
            coreA_.process(mono, aL, aR);
            coreB_.process(preDelay_.process(mono), bL, bR);

            // 两路直接相加（不做 ½ 归一）：实测第一路增益 α ≡ 1.000，
            // pv→0 时两路重合导致早期能量恰好翻倍（实测 2.000004 倍）。
            float wetL = aL + bL;
            float wetR = aR + bR;

            // 顺序：先 tilt 低架（候选侧的实现补偿），再 LOW/HIGH CUT
            // （对标参考的两条实测拐点律）。三者都是线性后置，数学上可交换；
            // 这样排是为了让「补偿」和「对标」在阅读上分层。
            wetL = highCut_[0].process(lowCut_[0].process(tilt_[0].process(wetL)));
            wetR = highCut_[1].process(lowCut_[1].process(tilt_[1].process(wetR)));

            chL[i] = dryGain_ * inL + wetGain_ * wetL;
            if (chR) chR[i] = dryGain_ * inR + wetGain_ * wetR;
        }
    }

private:
    void applyParams() noexcept
    {
        using namespace tuning;

        dryGain_ = static_cast<float>(dryGainFromNorm(nDryWet_));
        wetGain_ = static_cast<float>(wetGainFromNorm(nDryWet_)) * kWetTrim;

        const double ms = predelayMsFromNorm(nPreDelay_);
        preDelay_.setDelay(static_cast<int>(std::lround(ms * 1.0e-3 * sr_)));

        // 逐线反馈增益由 WetCore 自己按各条线长换算（不能共用一个 g ——
        // 线长差 2.4 倍，共用 g 会让 8 条线的每秒衰减率差 2.4 倍，
        // 尾巴变成多指数混合、T60 无定义。详见 WetCore::setDecay）。
        // 预算倍数的**逐档**修正在这里查表（表按 DECAY 归一值定义，
        // 而 WetCore 只看得到 T60 秒数，故不能在它内部反解）。
        const double t60 = t60FromDecaySec(decaySecFromNorm(nDecay_));
        const double budgetMul = t60BudgetScaleFromNorm(nDecay_);
        coreA_.setDecay(t60, sr_, budgetMul);
        coreB_.setDecay(t60, sr_, budgetMul);

        // 用**实测拐点律**驱动滤波器，而不是显示串上的 fc：
        // 实测显示值不是 −3 dB 点（lowcut=0 档真实拐点 19.1 Hz 而非 50 Hz，
        // highcut=1 档 12.1 kHz 而非 10 kHz）。详见 ReverbTuning.h 的推导。
        const double loHz = lowCutFcFromNorm(nLowCut_);
        const double hiHz = highCutFcFromNorm(nHighCut_);
        for (auto& f : lowCut_)  f.setHighpass(loHz, kFitLowCutQ, sr_);
        for (auto& f : highCut_) f.setLowpass(hiHz, kFitHighCutQ, sr_);

        // 低频倾斜补偿。**当前 kFitTiltShelfDb = 0，即这一级是直通**
        //（0 dB 低架 = 单位增益），留在信号链里只是为了将来重启时不用改结构。
        // 为什么停用：它当初修的低频「整体偏热」大半是插值损耗的镜像，
        // 真缺陷（ModulatedDelay 的线性插值）修好后它的最优深度自己塌回 0。
        // 完整推导见 ReverbTuning.h 的 kFitTiltShelf* 与 REFERENCE.md §10.2.2。
        //
        // 与参数无关（修的是本网络自身的频响，不随用户参数变化），但仍放在
        // 这里而不是 prepare()，因为 setLowShelf 需要 sr_，
        // 而 prepare 里 applyParams() 就在末尾调用。
        for (auto& f : tilt_)
            f.setLowShelf(kFitTiltShelfHz, kFitTiltShelfDb, kFitTiltShelfQ, sr_);
    }

    /// 湿声总增益的整体配平：把网络输出的绝对电平对到参考插件的湿声电平。
    ///
    /// 落点 0.668344（**−3.50 dB**）与 kFitTiltShelfDb 由
    /// tools/fit/fit_trim_tilt.py **联立**扫出。为什么必须联立：两者的作用区
    /// 部分重叠（低架在 235 Hz 以下全量衰减），单独扫会来回打架。
    ///
    /// 为什么是 −3.5 dB 这么大一刀：修掉 ModulatedDelay 的插值损耗并重标
    /// damping 之后（docs/REFERENCE.md §7.5/§7.6），逐带整段能量比从一条
    /// **倾斜**（250 Hz–8 kHz 离散 4.45 dB）变成一条几乎**平的 +3 dB 过量**
    /// —— 平的偏差正是静态增益能修的那一类。
    /// 注意这跟 §7.5「衰减率错不能用静态滤波修」不冲突：那条说的是**斜率**，
    /// 这条说的是斜率修好之后剩下的**电平**。
    ///
    /// 历史落点 1.018411 是插值损耗还在时标的 —— 那时高频本来就亏，
    /// 全带电平被拟合抬上去补偿，所以它偏高不是巧合。
    static constexpr float kWetTrim = 0.668344f;

    double sr_ { tuning::kRefSampleRate };

    WetCore coreA_, coreB_;
    VariableDelay preDelay_;
    std::array<Biquad, 2> lowCut_ {}, highCut_ {};
    /// 低频倾斜补偿（低架）。不对标参考的任何一个可调滤波器，
    /// 是候选侧的实现补偿，见 ReverbTuning.h 的 kFitTiltShelf*。
    std::array<Biquad, 2> tilt_ {};

    float nDryWet_ { 0.5f }, nPreDelay_ { 0.5f }, nDecay_ { 0.5f };
    float nLowCut_ { 0.0f }, nHighCut_ { 1.0f };
    float dryGain_ { 1.0f }, wetGain_ { 0.5f };
};

} // namespace nrev
