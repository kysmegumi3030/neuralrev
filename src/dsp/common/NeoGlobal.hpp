/*
  ==============================================================================

    Global.h
    Created: 11 Apr 2024 2:29:41pm
    Author:  Dell

  ==============================================================================
*/

#ifndef GLOBAL_HPP
#define GLOBAL_HPP

#include <array>
#include <chrono>
#ifndef _USE_MATH_DEFINES
#define _USE_MATH_DEFINES
#endif
#include <cmath>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <mutex>
#include <tuple>
#include <type_traits>
#include <vector>

#if defined(__GNUC__) || defined(__clang__)
#if defined(__aarch64__) || defined(_M_ARM64)
#define XSIMD_DEFAULT_ARCH neon64
#elif defined(__x86_64__) || defined(_M_X64)
#define XSIMD_DEFAULT_ARCH sse2
#else
#endif
#elif defined(_MSC_VER)
#if defined(__aarch64__) || defined(_M_ARM64)
#define XSIMD_DEFAULT_ARCH neon64
#elif defined(__x86_64__) || defined(_M_X64)
#define XSIMD_DEFAULT_ARCH sse4_2
#else
#endif
#endif
#include "xsimd/xsimd.hpp"

#if defined(__GNUC__) || defined(__clang__)
#define NOINLINE __attribute__((noinline))
#elif defined(_MSC_VER)
#define NOINLINE __declspec(noinline)
#else
#define NOINLINE
#endif

#if defined(__GNUC__) || defined(__clang__)
#ifdef _DEBUG
#define FORCE_INLINE  inline __attribute__((always_inline))
#define ALWAYS_INLINE __attribute__((always_inline))
#define LAMBDA_INLINE
#define NOINLINE      __attribute__((noinline))
#else
#define FORCE_INLINE  inline __attribute__((always_inline))
#define ALWAYS_INLINE __attribute__((always_inline))
#define LAMBDA_INLINE __attribute__((always_inline))
#define NOINLINE      __attribute__((noinline))
#endif
#if defined(EXPORT_DLL)
#define API_EXPORT __attribute__((visibility("default")))
#else
#define API_EXPORT
#endif
#elif defined(_MSC_VER)
#define FORCE_INLINE  __forceinline
#define ALWAYS_INLINE __forceinline
#define LAMBDA_INLINE
#define NOINLINE __declspec(noinline)
#if defined(EXPORT_DLL)
#define API_EXPORT __declspec(dllexport)
#else
#define API_EXPORT __declspec(dllimport)
#endif
#else
#define FORCE_INLINE inline
#define ALWAYS_INLINE
#define NOINLINE
#define API_EXPORT
#endif

// #define FORCE_INLINE

#if defined(__GNUC__) || defined(__clang__)
#if defined(__i386__) || defined(__x86_64__)
#include <xmmintrin.h>
#define PREFETCH(addr) _mm_prefetch((const char *)(addr), _MM_HINT_T0)
#elif defined(__arm__) || defined(__aarch64__)
#define PREFETCH(addr) __builtin_prefetch((addr), 0, 3)
#else
#define PREFETCH(addr) __builtin_prefetch((addr))
#endif
#elif defined(_MSC_VER)
#if defined(_M_IX86) || defined(_M_X64)
#include <xmmintrin.h>
#define PREFETCH(addr) _mm_prefetch((const char *)(addr), _MM_HINT_T0)
#else
#define PREFETCH(addr) __prefetch((addr))
#endif
#else
#define PREFETCH(addr) // 如果不支持预取，定义为空操作
#endif

#ifdef __clang__
#define restrict __restrict__
#else
#ifdef __GNUC__
#define restrict __restrict__
#endif
#endif
#ifdef _MSC_VER
#ifndef restrict
#define restrict __restrict
#endif
#endif
#ifndef restrict
#define restrict
#endif

#if defined(__clang__) || defined(__GNUC__) || defined(__INTEL_COMPILER)
#define FASTOPT_ATTR(level)  __attribute__((optimize(level)))
#define FASTOPT_BEGIN(level) _Pragma("GCC push_options") _Pragma(level)
#define FASTOPT_END          _Pragma("GCC pop_options")
#elif defined(_MSC_VER)
#define FASTOPT_ATTR(level)
#define FASTOPT_BEGIN(level) __pragma(optimize(level, on))
#define FASTOPT_END          __pragma(optimize("", on))
#else
#define FASTOPT_ATTR(level)
#define FASTOPT_BEGIN(level)
#define FASTOPT_END
#endif

#if defined(_MSC_VER)
#define LOOP_UNROLL(n)        __pragma(loop(unroll_count(n)))
#define LOOP_UNROLL_FULL()    __pragma(loop(no_vector))
#define LOOP_UNROLL_DISABLE() __pragma(loop(unroll_count(1)))
#elif defined(__clang__)
#define STRINGIFY(x)                #x
#define LOOP_UNROLL_IMPL(directive) _Pragma(directive)
#define LOOP_UNROLL(n)              LOOP_UNROLL_IMPL(STRINGIFY(clang loop unroll_count(n)))
#define LOOP_UNROLL_FULL()          _Pragma("clang loop unroll(full)")
#define LOOP_UNROLL_DISABLE()       _Pragma("clang loop unroll(disable)")
#elif defined(__GNUC__)
#if __GNUC__ >= 8
#define STRINGIFY(x)                #x
#define LOOP_UNROLL_IMPL(directive) _Pragma(directive)
#define LOOP_UNROLL(n)              LOOP_UNROLL_IMPL(STRINGIFY(GCC unroll n))
#define LOOP_UNROLL_FULL()          _Pragma("GCC unroll 65534") // 大数值表示完全展开
#define LOOP_UNROLL_DISABLE()       _Pragma("GCC unroll 1")
#else
// 旧版本 GCC 不支持，使用空宏
#define LOOP_UNROLL(n)
#define LOOP_UNROLL_FULL()
#define LOOP_UNROLL_DISABLE()
#endif
#else
// 未知编译器，使用空宏
#define LOOP_UNROLL(n)
#define LOOP_UNROLL_FULL()
#define LOOP_UNROLL_DISABLE()
#endif

constexpr int32_t NeoEqFreqzPoints = 512;
constexpr double e_v = (2.718281828459045);
constexpr double log2e_v = (1.4426950408889634);
constexpr double log10e_v = (0.4342944819032518);
constexpr double pi_v = (3.141592653589793);
constexpr double inv_pi_v = (0.3183098861837907);
constexpr double inv_sqrtpi_v = (0.5641895835477563);
constexpr double ln2_v = (0.69314718055994530942);
constexpr double ln10_v = (2.30258509299404568402);
constexpr double sqrt2_v = (1.4142135623730950488);
constexpr double sqrt3_v = (1.7320508075688772);
constexpr double inv_sqrt3_v = (0.5773502691896257);
constexpr double egamma_v = (0.5772156649015329);
constexpr double phi_v = (1.618033988749895);

#define USE_XSIMD

constexpr uint32_t next_power_of_two(uint32_t n) {
	n--;
	n |= n >> 1u;
	n |= n >> 2u;
	n |= n >> 4u;
	n |= n >> 8u;
	n |= n >> 16u;
	n++;
	return n;
}

static float inv_fast(const float c) noexcept {
	// f(x) = 1/x - c; x is the expected inv of c, since c is the known const
	union {
		float f;
		int i;
	} v;
	v.i = (int)(0x7EF127EA - *(uint32_t *)&c);
	float x0 = v.f;
	float x1 = x0 * (2.0f - c * x0);
	float x2 = x1 * (2.0f - c * x1);
	// float x3 = x2 * (2.0f - c * x2);
	// float x4 = x3 * (2.0f - c * x3);
	// float x5 = x3 * (2.0f - c * x4);
	// float x6 = x3 * (2.0f - c * x5);
	// float x7 = x3 * (2.0f - c * x6);
	// float x8 = x3 * (2.0f - c * x7);
	return x2;
}

template <typename T>
constexpr T DB_CO(T g) {
	return exp(ln10_v * g * 0.05f);
}
template <typename T>
constexpr T DB_CO_INF(T g) {
	return g <= -89.0f ? 0 : exp(ln10_v * g * 0.05f);
}

template <typename T>
constexpr T CO_DB(T g) {
	return 20.f * std::log10(g);
}

template <typename T>
inline T fast_tan(T x) {
	constexpr auto calc = [](auto x) { return (105 * x - 10 * x * x * x) / (105 - 45 * x * x + x * x * x * x); };
	return std::invoke(calc, x);
}

template <typename T>
FORCE_INLINE T fast_sin_algo(const T x) noexcept {
	const auto x2 = x * x;
	// return x * (-4469552712 * x6 + 538531796880 * x4 - 17478564143040 * x2 + 124345562140800) / (1768969 * x8 + 366075360 * x6 + 43268148000 * x4 + 3245696213760 * x2 + 124345562140800);
	// return x * (((-479249 * x2 + 52785432) * x2 - 1640635920) * x2 + 11511339840) / (((18361 * x2 + 3177720) * x2 + 277920720) * x2 + 11511339840);
	return x * ((532182 * x2 - 23819040) * x2 + 183284640) /
	       (((1331 * x2 + 126210) * x2 + 6728400) * x2 + 183284640); // 已经到达float的极限
	                                                                 // return x * (166320 + (-22260 + 551 * x2) * x2) * inv_fast(166320 + (5460 + 75 * x2) * x2);
	                                                                 // return x * (5880 - 620 * x2) * inv_fast(5880 + (360 + 11 * x2) * x2); // -70dB 信噪比
	                                                                 // return x * (60 - 7 * x2) * inv_fast(60 + 3 * x2);// 完全不能用！
};

template <typename T>
FORCE_INLINE T fast_sin(const T x) noexcept {
	auto a = abs(x);
	a = (T)M_PI_2 - abs((T)M_PI_2 - a);
	return copysign(fast_sin_algo(a), x);
}

template <typename T>
constexpr T fast_cos(T x) {
	constexpr auto calc = [](auto x) {
		auto x2 = x * x;
		return ((((-14615.0 * x2) + 1075032.0) * x2 - 18471600.0) * x2 + 39251520.0) / ((((127.0 * x2 + 16632.0) * x2 + 16632.0) * x2 + 1154160.0) * x2 + 39251520.0);
	};
	// auto a = abs(x);
	// a = (T)M_PI_2 - abs((T)M_PI_2 - a);
	return calc(x);
}

template <typename T>
static T bessel_i0_approx(uint32_t order, T x) {
	double sum = 1.0;
	double term = 1.0;
	for (uint32_t k = 1; k <= order; ++k) { // 控制逼近精度
		term *= (x * x) / (4 * k * k);
		sum += term;
	}
	return sum;
}

template <typename T>
inline T reducePrecision(T value, int bitsToKeep) {
	// static_assert(std::is_floating_point<T>::value, "Type must be floating point");

	using IntType = typename std::conditional<sizeof(T) == 8, uint64_t, uint32_t>::type;

	auto *intPtr = reinterpret_cast<IntType *>(&value);
	IntType intValue = *intPtr;

	// int totalBits = sizeof(T) * 8;
	int mantissaBits = std::numeric_limits<T>::digits - 1;
	int bitsToZero = mantissaBits - bitsToKeep;

	if (bitsToZero >= 0 && bitsToZero <= mantissaBits) {
		IntType mask = ~((IntType(1) << bitsToZero) - 1);

		*intPtr = intValue & mask;
	}

	return value;
}
// --------------------------------------------------------------------------------------------------------------------

template <typename T>
class CircularBuffer {
public:
	CircularBuffer(uint32_t size = 512) : buffer_(next_power_of_two(size) << 1, T{}), size_{next_power_of_two(size)}, index_{0} {}
	void push(const T &value) {
		buffer_[index_] = buffer_[index_ + size_] = value;
		index_ = (index_ + 1) % size_;
	}

	void push(const T *values, uint32_t count) {
		for (uint32_t i = 0; i < count; ++i) {
			buffer_[(index_ + i) % size_] = values[i];
			buffer_[(index_ + i + size_) % size_] = values[i];
		}
		index_ = (index_ + count) % size_;
	}

	T *getData() { return buffer_.data() + index_; }

	T getValue() { return buffer_[index_]; }

	uint32_t getIndex() { return index_; }

	void resize(uint32_t size) {
		size_ = next_power_of_two(size);
		buffer_.resize(size_ << 1, T{});
		std::fill(buffer_.begin(), buffer_.end(), T{});
		index_ = 0;
	}

	uint32_t size() const { return size_; }

private:
	std::vector<T, std::allocator<T>> buffer_;
	uint32_t size_;
	uint32_t index_;
};

template <typename T, bool use2order = true>
class GradualLimit {

public:
	static constexpr int32_t DEFAULT_SAMPLE_RATE = 48000;
	static constexpr float DEFAULT_ATTACK_TIME = 0.01f;

	// 构造函数
	GradualLimit(T prev = T{}) : GradualLimit(prev, DEFAULT_SAMPLE_RATE, DEFAULT_ATTACK_TIME) {}

	GradualLimit(T prev, T sampleRate, T attackTime) : state_{prev, sampleRate, attackTime, T{}, prev, prev} { updateCoefficient(); }
	GradualLimit(const GradualLimit &other) = default;
	GradualLimit(GradualLimit &&other) noexcept = default;

	// 状态结构
	struct State {
		T target_value;
		T sample_rate;
		T attack_time;
		T attack_coeff;
		T prev;
		T prev1;
		bool flagUpdate = 0;
	};

	// 获取状态
	[[nodiscard]] const auto &getState() const noexcept { return state_; }

	// 准备函数
	void prepare(T sampleRate, T attackTime, T prev) {
		state_.sample_rate = sampleRate;
		state_.attack_time = attackTime;
		state_.prev = prev;
		state_.prev1 = prev;
		state_.target_value = prev;
		updateCoefficient();
	}

	// 设置采样率
	int32_t setSampleRate(T sampleRate) noexcept {
		state_.sample_rate = sampleRate;
		updateCoefficient();
		return 0;
	}

	// 设置攻击时间
	int32_t setAttackTime(T attackTime) noexcept {
		state_.attack_time = attackTime;
		updateCoefficient();
		return 0;
	}

	void setAttackCoeff(T attackCoeff) noexcept { state_.attack_coeff = attackCoeff; }

	// 设置目标值
	void setTargetValue(T targetValue) noexcept {
		state_.target_value = targetValue;
		state_.flagUpdate = 1;
	}

	[[nodiscard]] T& getTargetValue() noexcept { return state_.target_value; }

	// 获取当前值
	[[nodiscard]] T& getCurrentValue() noexcept { return state_.prev; }

	void setAndSync(T targetValue) {
		state_.prev = targetValue;
		state_.prev1 = targetValue;
		state_.target_value = targetValue;
		state_.flagUpdate = 0;
	}

	void sync() {
		state_.prev = state_.target_value;
		state_.prev1 = state_.target_value;
		state_.flagUpdate = 0;
	}

	// 更新函数
	void update() noexcept {
		// state_.prev1 += state_.attack_coeff *
		//             (state_.target_value - state_.prev1);
		// auto diff = state_.prev1 - state_.prev;
		// state_.prev += state_.attack_coeff *
		//            	(state_.prev1 - state_.prev);
		if (state_.flagUpdate) [[unlikely]] {
			if constexpr (use2order) {
				state_.prev1 += state_.attack_coeff * (state_.target_value - state_.prev1);
				state_.prev += state_.attack_coeff * (state_.prev1 - state_.prev);
				state_.flagUpdate = abs((state_.target_value - state_.prev)) > abs(0.001f * state_.prev);
				if(!state_.flagUpdate) { state_.prev = state_.target_value; }
			} else {
				state_.prev += state_.attack_coeff * (state_.target_value - state_.prev);
				state_.flagUpdate = abs((state_.target_value - state_.prev)) > abs(0.001f * state_.prev);
				if(!state_.flagUpdate) { state_.prev = state_.target_value; }
			}
		}

		return;
	}

	// 拷贝赋值
	GradualLimit &operator=(const GradualLimit &other) = default;

	// 移动操作
	GradualLimit &operator=(GradualLimit &&) noexcept = default;

	// 类型转换操作符
	operator T() noexcept {
		update();
		return getCurrentValue();
	}

	T operator()() noexcept {
		update();
		return getCurrentValue();
	}

	// 赋值操作符
	GradualLimit &operator=(T value) noexcept {
		setTargetValue(value);
		return *this;
	}

private:
	void updateCoefficient() noexcept {
		state_.attack_coeff = static_cast<T>(1.0 - exp(-1.0 / (state_.sample_rate * state_.attack_time)));
	}

	State state_;
};

template <typename T, bool gradual = true>
class GradualLimitLinear {

public:
	static constexpr int DEFAULT_STEPS = 64; // 默认步数

	// 构造函数
	GradualLimitLinear(T prev = T{}) : GradualLimitLinear(prev, DEFAULT_STEPS) {}

	GradualLimitLinear(T prev, int32_t steps) : state_{prev, prev, T{}, steps, 0} {}

	GradualLimitLinear(const GradualLimitLinear &other) = default;
	GradualLimitLinear(GradualLimitLinear &&other) noexcept = default;

	// 状态结构
	struct State {
		T current_value;     // 当前值
		T target_value;      // 目标值
		T step_size;         // 每步变化量
		int32_t total_steps; // 总步数设置
		int32_t steps_left;  // 剩余步数
	};

	// 获取状态
	[[nodiscard]] State getState() const noexcept { return state_; }

	// 准备函数
	void prepare(T initialValue, T steps) {
		state_.current_value = initialValue;
		state_.target_value = initialValue;
		state_.total_steps = steps;
		state_.steps_left = 0;
		state_.flag = 0;
	}

	// 设置步数
	int32_t setSteps(T steps) noexcept {
		if (steps <= 0) return -1;
		state_.total_steps = steps;
		return 0;
	}

	bool isUpdating() const noexcept { return state_.steps_left > 0; }

	// 设置目标值
	void setTargetValue(T targetValue) noexcept {
		// if (state_.steps_left) return;
		if constexpr (gradual) {
			state_.target_value = targetValue;
			state_.steps_left = static_cast<int32_t>(state_.total_steps);
			if (state_.steps_left > 0) { state_.step_size = (state_.target_value - state_.current_value) / state_.steps_left; }
		} else {
			state_.current_value = targetValue;
			state_.target_value = targetValue;
			state_.steps_left = 0;
			state_.step_size = 1;
		}
	}

	[[nodiscard]] T getTargetValue() const noexcept { return state_.target_value; }

	// 获取当前值
	[[nodiscard]] T getCurrentValue() const noexcept { return state_.current_value; }

	void setAndSync(T targetValue) {
		state_.current_value = targetValue;
		state_.target_value = targetValue;
		state_.steps_left = 0;
	}

	// void sync() {
	//     state_.current_value = state_.target_value;
	//     state_.steps_left = 0;
	//     state_.flag = 0;
	// }

	// 更新函数
	FORCE_INLINE void update() noexcept {
		if (state_.steps_left > 0) {
			state_.current_value += state_.step_size;
			state_.steps_left--;

			// 最后一步直接赋值，避免累计误差
			if (state_.steps_left == 0) { state_.current_value = state_.target_value; }
		}
		return;
	}

	FORCE_INLINE T forceUpdate() noexcept { // this should only be used in EQ to avoid useless if check
		state_.current_value += state_.step_size;
		state_.steps_left--;

		// 最后一步直接赋值，避免累计误差
		if (state_.steps_left == 0) { state_.current_value = state_.target_value; }

		return state_.current_value;
	}

	// 拷贝赋值
	GradualLimitLinear &operator=(const GradualLimitLinear &other) = default;

	// 移动操作
	GradualLimitLinear &operator=(GradualLimitLinear &&) noexcept = default;

	// 类型转换操作符
	operator T() noexcept {
		update();
		return getCurrentValue();
	}

	T operator()() noexcept {
		update();
		return getCurrentValue();
	}

	// 赋值操作符
	GradualLimitLinear &operator=(T value) noexcept {
		setTargetValue(value);
		return *this;
	}

	void sync() {
		state_.current_value = state_.target_value;
		state_.steps_left = 0;
	}

private:
	State state_;
};

template <typename T>
inline int circshift(const T in[], int L, int shift, T out[]) {
	int p;
	// Fix shift
	p = (L - shift) % L;

	if (p < 0) p += L;

	if (in == out) {
		if (p) // Do nothing if no shift is needed
		{
			int m, count, i, j;

			// Circshift inplace is magic!
			for (m = 0, count = 0; count != L; m++) {
				T t = in[m];

				for (i = m, j = m + p; j != m; i = j, j = j + p < L ? j + p : j + p - L, count++) out[i] = out[j];

				out[i] = t;
				count++;
			}
		}
	} else {
		// Still ok if p==0
		memcpy(out, in + p, (L - p) * sizeof *out);
		memcpy(out + L - p, in, p * sizeof *out);
	}
	return 0;
}

#endif
