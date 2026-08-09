/**
 * @file  DelayEffect.h
 * @brief 延迟效果器（JUCE 侧封装）：参数换算 + 双声道延迟线 + 环内滤波 + 干湿混合
 *
 * 信号流（拓扑逐条实测确定，判据见 DelayTuning.h 顶部注释）：
 *
 *   in ──┬─────────────────────────────────── ×dry ──────────────────┬── out
 *        │                                                          │
 *        └─→ [延迟线 D，LFO 调制写指针] → [FIR] → [HP] → [LP] ─┬─→ [预延迟 20.509] → ×wet → [饱和] ─┘
 *                    ↑                                        │
 *                    └────────────────── ×fb ─────────────────┘
 *
 * 注意那 20.509 样点的固定预延迟在**抽头之后**、不在环内：逐圈累积延迟的增量
 * 把这两种拓扑分开了（参考 11.37 样点/圈 = FIR 群延迟，而不是 16+gd），
 * 依据见 DelayCore.hpp 的 WetPreDelay。它 = 整数 16（onset 判据）+ 分数 4.509
 * （反卷积判据）；两个判据各自只能定一半，见 DelayTuning.h 的
 * kMeasWetPreDelayFrac。
 *
 * ×fb 里含环内平项 0.995283：抽头表已归一到 DC 增益 1，那 0.45% 的平损不在
 * 核里。实测候选每圈曾高 0.497%，反解 0.995052 —— 与常数差 0.02%。
 *
 * 与 ReverbEffect.h 同一套接口约定：prepare / reset / process，
 * 外加 setParametersNormalized()（供离线渲染器与 A/B 脚本按归一值驱动，
 * 与参考插件的 VST3 normalized 语义一致 —— 保证「测的就是发布的」）。
 */

#pragma once

#ifndef NREV_NO_JUCE
#include <juce_dsp/juce_dsp.h>
#endif

#include "DelayCore.hpp"
#include "DelayTuning.h"

namespace nrev
{

class DelayEffect
{
public:
    void prepare(const juce::dsp::ProcessSpec& spec)
    {
        sr_ = spec.sampleRate;

        const int maxD = static_cast<int>(std::ceil(
            delaytuning::kMeasTimeMaxMs * 1.0e-3 * sr_)) + 8;
        // LFO 摆幅余量：净调制峰值最大 2A（约 6.6 样点），
        // 写入侧单边 A，取 4A 作余量，足够且不浪费。
        const int head = static_cast<int>(std::ceil(
            4.0 * delaytuning::kMeasLfoAmpSamples)) + 8;

        for (auto& l : line_) l.prepare(maxD, head);

        // 干路延迟：48 kHz 上是实测的 51 样点，其它率按比例折算并四舍五入。
        // 折算而不是照抄：这一项的物理身份是**时间**（1.0625 ms），
        // 而参考在非 48 kHz 上自报 63/114/120 —— 与按比例的 46.9/93.7/102
        // 都不吻合（参考内部在重采样，见 DelayTuning.h 的 ⚠️）。
        // 这里取时间不变，因为那是唯一有实测支撑的解释。
        dryLatency_ = static_cast<int>(std::lround(
            delaytuning::kMeasDryLatencySamples48k * sr_ / delaytuning::kRefSampleRate));
        for (auto& d : dryLat_) d.prepare(dryLatency_);

        reset();
        applyParams();
    }

    /// LFO 起相（周期的分数）。只有对拍脚本用它标定参考侧的未知起相；
    /// 插件里保持 0（LFO 锚定在渲染起点，见 DelayCore.hpp setPhase 的注释）。
    void setLfoPhase(double frac) noexcept
    {
        for (auto& l : line_) l.setPhase(frac);
    }

    void reset()
    {
        for (auto& l : line_) l.reset();
        for (auto& f : loopLp_) f.reset();
        for (auto& f : loopHp_) f.reset();
        for (auto& f : fixedFir_) f.reset();
        for (auto& d : wetPre_) d.reset();
        for (auto& d : dryLat_) d.reset();
        fbState_[0] = fbState_[1] = 0.0f;
    }

    // ------------------------------------------------------------
    // 参数设置（归一值 0..1，与参考插件的 VST3 normalized 一致）
    // ------------------------------------------------------------
    void setParametersNormalized(float dryWet, float timeL, float timeR,
                                 float feedback, float lowpass, float highpass,
                                 float stereoMode) noexcept
    {
        nDryWet_ = dryWet;
        nTimeL_ = timeL;
        nTimeR_ = timeR;
        nFeedback_ = feedback;
        nLowpass_ = lowpass;
        nHighpass_ = highpass;
        nMode_ = stereoMode;
        applyParams();
    }

    /// 以真实工程量设置（供 UI/APVTS 直接喂真实值）
    void setParameters(float dryWet, float timeLms, float timeRms,
                       float feedbackDisplay, float lowpassHz, float highpassHz,
                       bool stereo) noexcept
    {
        using namespace delaytuning;
        nDryWet_ = dryWet;
        nTimeL_ = static_cast<float>(timeNormFromMs(timeLms));
        nTimeR_ = static_cast<float>(timeNormFromMs(timeRms));
        // 显示 0.00–0.50 → 归一 0–1
        nFeedback_ = static_cast<float>(
            std::clamp(static_cast<double>(feedbackDisplay) / kMeasFeedbackDisplayMax,
                       0.0, 1.0));
        nLowpass_ = static_cast<float>(lowpassNormFromHz(lowpassHz));
        nHighpass_ = static_cast<float>(highpassNormFromHz(highpassHz));
        nMode_ = stereo ? 1.0f : 0.0f;
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

        const bool stereo = (nMode_ >= 0.5f);

        for (size_t i = 0; i < numSamples; ++i)
        {
            const float inL = chL[i];
            const float inR = chR ? chR[i] : inL;

            float wetL = 0.0f, wetR = 0.0f;

            if (stereo)
            {
                // Stereo：两条独立延迟线，**零交叉馈送**（实测 R@Dl = 0.000e+00）
                wetL = line_[0].process(inL + fbState_[0]);
                wetR = line_[1].process(inR + fbState_[1]);
            }
            else
            {
                // Mono：两路输入**取平均**（不是求和）喂一条线，两输出取同一条。
                //
                // ⚠️ 原先写 inL + inR，依据是「两输出在同一延迟处给出同一峰值」
                // —— 那条只证明**两个输出相等**，对系数 1 还是 1/2 完全是盲的
                // （两种做法都给出两路相等的输出）。§14.11 同型：判据对要判的
                // 那个自由度不敏感。
                //
                // 能判的读数：同一噪声突发、只改 delay_mode，比较**湿声 rms**。
                // 参考 Mono 与 Stereo 的 rms 相等（5.4543e−03 vs 5.4544e−03，
                // 差 0.002%），即参考在 Mono 下**不会**因为两路相加而响一倍；
                // 候选当时是 1.1066e−02，正好 2.0288 倍。取平均后两侧同标度。
                //
                // 注意 ab_delay.py 的波形口径对这一项是盲的：waveform_diff 会
                // 先解一个最佳标量增益再算残差，整体电平差被除掉了（Mono 档
                // 报 gain=0.4974 就是这个 2 倍被吸收成了配平因子）。
                const float sum = 0.5f * (inL + inR);
                wetL = line_[0].process(sum + fbState_[0]);
                wetR = wetL;
            }

            // 环内滤波：固定级联 → HP → LP。四者都是线性、数学上可交换，
            // 这样排是为了与实测的叙述对齐。
            //
            // ⚠️ **湿声抽头在滤波之后**，不是之前。这一条被改过一次：
            // 原先只把滤波放在反馈支路里，于是候选的**第一次**回声完全没被
            // 滤过，而参考的第一次回声是滤过的。判据（两个，都是直接读数）：
            //   * LP=0.4（名义 fc 3046 Hz）时，参考的 **echo1** 在 3 kHz 上
            //     就已经是 +0.03 dB vs LP=1.0 档的 +2.90 dB —— 差 2.87 dB，
            //     正是那个二阶 Butterworth 在自己 fc 上的 −3 dB。若抽头在
            //     滤波前，echo1 必须与 LP 档位**无关**。
            //   * 单冲激下参考的峰位比理论 D 晚 22…28 样点（各档），而候选
            //     只晚 3 样点 —— 那 25 样点正是这四级二阶的群延迟。
            // 「逐次回声谱严格 ×k 累积」仍然成立：echo n 过 n 次滤波。
            const float filtL = loopLp_[0].process(loopHp_[0].process(
                fixedFir_[0].process(wetL)));
            const float filtR = stereo
                ? loopLp_[1].process(loopHp_[1].process(
                      fixedFir_[1].process(wetR)))
                : filtL;

            // 反馈标量只有量化后的 fb_。平项**不再单独乘** —— 它已经含在
            // FIR 的 DC 增益里（抽头之和 1.5959，而非 2×0.8 = 1.6）。
            fbState_[0] = fb_ * filtL;
            if (stereo)
                fbState_[1] = fb_ * filtR;

            // 固定 20.509 样点预延迟：**在湿抽头上、不在反馈支路里**。
            // 这一条是量出来的（逐圈增量把两种拓扑分开，见 DelayCore.hpp
            // WetPreDelay 的注释）：参考每圈只累积 FIR 群延迟 11.37 样点，
            // 而这一段只在输出路径上过一次。放进环里会让每圈多吃一个常数，
            // 症状是 fb=1.0 档逐圈滞后线性累积。
            //
            // 放在 ×wet 与饱和之前：纯延迟与逐样点无记忆的非线性可交换，
            // 所以这三者的先后不影响结果，此处按信号流顺序写。
            const float preL = wetPre_[0].process(filtL);
            const float preR = stereo ? wetPre_[1].process(filtR) : preL;

            // 湿声总线：×wet 后过静态饱和（实测饱和在环外、只在湿路）
            const float outWetL = sat_.process(wetGain_ * preL);
            const float outWetR = sat_.process(wetGain_ * preR);

            // 干路**延后 51 样点**（48 kHz），湿路不延。这不是拟合出来的
            // 配平，是参考通过 getLatencySamples() 自报的量，且实测干路冲激
            // 恰好落在 AT+51、单样点无拖尾。见 DelayTuning.h 的
            // kMeasDryLatencySamples48k。
            //
            // ⚠️ 不能因为「DAW 会做延迟补偿」就省掉它：宿主补偿平移的是
            // **整个输出**，干湿的相对关系不变。参考的干比它自己的湿晚
            // 51 样点，那是 1.06 ms 的相对错位（梳状效应，听得见）。
            //
            // 这一项之前缺失，是 12 档对拍里**唯一**的波形超标档（干湿 0.5：
            // max|Δ|=1.659e−03，lag=−51 —— 那个 lag 就是它的签名）。
            // 注意波形口径本身对它半盲：waveform_diff 会先做整数对齐，
            // 把 lag 吸收掉，剩下的错位才体现为残差。
            chL[i] = dryLat_[0].process(dryGain_ * inL) + outWetL;
            if (chR) chR[i] = dryLat_[1].process(dryGain_ * inR) + outWetR;
        }
    }

private:
    void applyParams() noexcept
    {
        using namespace delaytuning;

        dryGain_ = static_cast<float>(dryGainFromNorm(nDryWet_));
        wetGain_ = static_cast<float>(wetGainFromNorm(nDryWet_));

        // 反馈：**按显示两位小数量化**后再乘上限 0.80。
        // 量化是实测的（同格内系数相同到 0.0002%），不是保守取整。
        //
        // 再乘环内平项 kFitLoopFlatGain（0.995283）：抽头表已归一到 DC 增益 1，
        // 那 0.45% 的平损不在核里，必须单独乘回来（见 DelayTuning.h 的 ⚠️）。
        // 乘在**反馈支路**而不是滤波器输出上：这一项是从 L(f)（逐圈环路传递）
        // 测出来的，是个逐圈量；乘在滤波器输出上会连 echo1 一起压低 0.47%，
        // 而 echo1 的电平由 wetGain_ 单独标定过，那样会破坏已闭合的读数。
        // 判据是两个读数必须同时闭合：逐圈增益比 → 1.000，且 echo1 峰值比不退。
        fb_ = static_cast<float>(feedbackFromNorm(nFeedback_) * kFitLoopFlatGain);

        // 环内额外的 kFitLoopExtraDelay（0.4017 样点）加在**延迟线基准**上，
        // 于是它每圈各计一次 —— 这正是它的物理身份（逐圈量）。
        //
        // 为什么不加在 WetPreDelay 上：那里只过一次，加了只动 echo1，
        // 而实测说 echo1 已经闭合（峰位差 −0.034）、第 2 圈起才短。
        // 判据是两个读数必须**同时**闭合：逐圈重心回归斜率 → 0，且 echo1 不退。
        const double dL = timeMsFromNorm(nTimeL_) * 1.0e-3 * sr_ + kFitLoopExtraDelay;
        const double dR = timeMsFromNorm(nTimeR_) * 1.0e-3 * sr_ + kFitLoopExtraDelay;
        line_[0].setDelay(dL);
        line_[1].setDelay(dR);

        for (auto& l : line_)
            l.setLfo(kMeasLfoRateHz, kMeasLfoAmpSamples, sr_);

        const double lpHz = lowpassHzFromNorm(nLowpass_);
        const double hpHz = highpassHzFromNorm(nHighpass_);
        // 用户 LP/HP：二阶 Butterworth，**直接用显示 fc** ——
        // 实测三个档位在各自名义 fc 上都恰好 −3 dB（与混响段相反，不能类推）。
        for (auto& f : loopLp_) f.setLowpass(lpHz, kMeasUserFilterQ, sr_);
        for (auto& f : loopHp_) f.setHighpass(hpHz, kMeasUserFilterQ, sr_);

        // 环内**固定**损耗（抗镜像核）用 FIR，抽头是编译期常量、无需 setup。
        // 它不是极点滤波器：级联二阶最好只到 2.97 dB，而 FIR 到 12 kHz
        // 是 0.043 dB，且自带实测的 22 样点群延迟。见 DelayTuning.h。

        // 饱和的静态增益取 1：线性区的电平已经由 wetGain_（= 1.943552·min(dw,0.82)）
        // 定死了，这里再乘一次就会重复计入。饱和只负责**弯曲**。
        sat_.setDrive(1.0, kFitSatDriveK);
    }

    double sr_ { delaytuning::kRefSampleRate };

    std::array<LfoDelayLine, 2> line_ {};
    /// 用户 LP/HP（二阶 Butterworth，显示 fc 诚实）
    std::array<DelayBiquad, 2> loopLp_ {}, loopHp_ {};
    /// 环内固定损耗（抗镜像核）：28 抽头 FIR，抽头是直接实测的。
    /// 见 DelayTuning.h kMeasLoopFirTaps —— 换掉了原来的两级二阶级联。
    std::array<LoopFir, 2> fixedFir_ {};
    /// 固定 20.509 样点预延迟：只在湿抽头上过一次，**不在反馈环内**。
    std::array<WetPreDelay, 2> wetPre_ {};
    /// 干路的 51 样点整数延迟（参考自报的延迟补偿量）
    std::array<DryLatency, 2> dryLat_ {};
    int dryLatency_ { delaytuning::kMeasDryLatencySamples48k };
    WetSaturator sat_ {};
    std::array<float, 2> fbState_ { 0.0f, 0.0f };

    float nDryWet_ { 0.5f };
    float nTimeL_ { 0.577079952f }, nTimeR_ { 0.577079952f };  // 出厂默认 500 ms
    float nFeedback_ { 0.5f };                                  // 显示 0.25
    float nLowpass_ { 1.0f }, nHighpass_ { 0.0f };
    float nMode_ { 1.0f };                                      // Stereo

    float dryGain_ { 1.0f }, wetGain_ { 0.5f }, fb_ { 0.4f };
};

} // namespace nrev
