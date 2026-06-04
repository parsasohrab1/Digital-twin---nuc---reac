import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# =====================================================
# پارامترهای ثابت راکتور PWR (مرجع: VVER-1000 / Westinghouse)
# =====================================================

class ReactorParams:
    # مشخصات هندسی و عملیاتی قلب راکتور
    core_height = 3.66  # متر
    core_equivalent_diameter = 3.04  # متر
    num_fuel_assemblies = 163  # تعداد مجتمع‌های سوخت
    fuel_rods_per_assembly = 264  # تعداد میله سوخت در هر مجتمع
    
    # شرایط نامی (نرمال)
    nominal_power = 3000.0  # MWth (توان حرارتی)
    nominal_pressure = 15.5  # MPa (فشار مدار اول)
    nominal_inlet_temp = 290.0  # درجه سانتی‌گراد
    nominal_outlet_temp = 330.0  # درجه سانتی‌گراد
    nominal_flow_rate = 18.5  # m³/s (دبی حجمی)
    nominal_mass_flow = 18500.0  # kg/s (دبی جرمی)
    
    # محدوده مجاز ایمنی
    max_clad_temp = 620.0  # درجه سانتی‌گراد (حد مجاز غلاف سوخت)
    max_coolant_temp = 350.0  # درجه سانتی‌گراد
    min_dnbr = 1.3  # حداقل نسبت ایمنی جوش بحرانی
    max_pressure_drop = 0.3  # MPa (افت فشار مجاز قلب)

# =====================================================
# توابع تولید نویز و روندهای فیزیکی
# =====================================================

def add_noise(value, noise_percent=0.5):
    """افزودن نویز گاوسی به مقدار"""
    noise = np.random.normal(0, noise_percent/100 * abs(value))
    return value + noise

def generate_degradation_trend(initial, final, length, start_idx, degradation_type='linear', noise=0.01):
    """
    تولید روند تخریب (رسوب، خوردگی، زبری)
    degradation_type: 'linear', 'exponential', 'saturation'
    """
    t = np.linspace(0, 1, length)
    if degradation_type == 'linear':
        trend = initial + (final - initial) * t
    elif degradation_type == 'exponential':
        trend = initial + (final - initial) * (1 - np.exp(-3*t))
    else:  # saturation
        trend = initial + (final - initial) * (1 - (1-t)**2)
    
    noise_arr = np.random.normal(0, noise * (final - initial), length)
    return trend + noise_arr

# =====================================================
# کلاس اصلی تولید داده سنتتیک راکتور
# =====================================================

class SyntheticReactorDataGenerator:
    def __init__(self, duration_days=30, time_step_sec=1):
        self.duration_days = duration_days
        self.time_step_sec = time_step_sec
        self.n_steps = int(duration_days * 24 * 3600 / time_step_sec)
        self.params = ReactorParams()
        
        # ذخیره‌سازی داده‌ها
        self.data = {}
        
        # وضعیت اولیه
        self._initialize_state()
        
        # تعریف سناریوهای گذرا
        self._define_transients()
        
        # روندهای تخریب پارامترهای فیزیکی (برای EKF و پیش‌بینی خطا)
        self._define_degradation_trends()
        
    def _initialize_state(self):
        """مقداردهی اولیه متغیرهای حالت"""
        self.state = {
            # متغیرهای اصلی حرارتی-هیدرولیکی
            'power': self.params.nominal_power,  # MWth
            'pressure_primary': self.params.nominal_pressure,  # MPa
            'temp_inlet': self.params.nominal_inlet_temp,  # °C
            'temp_outlet': self.params.nominal_outlet_temp,  # °C
            'temp_core_avg': (self.params.nominal_inlet_temp + self.params.nominal_outlet_temp) / 2,
            'mass_flow_rate': self.params.nominal_mass_flow,  # kg/s
            'volumetric_flow_rate': self.params.nominal_flow_rate,  # m³/s
            
            # توزیع دما در قلب (شبیه‌سازی نقاط کلیدی)
            'temp_hot_channel': self.params.nominal_outlet_temp + 15,  # داغ‌ترین مجتمع
            'temp_cladding_max': 380.0,  # دمای بیشینه غلاف سوخت
            'dnbr': 1.8,  # نسبت ایمنی جوش بحرانی
            
            # پارامترهای فیزیکی نامعلوم (برای EKF)
            'roughness_factor': 0.05,  # mm (ضریب زبری میله سوخت)
            'fouling_resistance': 0.0001,  # m²K/W (مقاومت رسوب)
            'pressure_drop_coefficient': 1.0,  # ضریب افت فشار
            
            # متغیرهای نوترونی
            'neutron_flux_axial': 1.0,  # نرمال‌شده به میانگین
            'neutron_flux_radial': 1.0,
            'boron_concentration': 600.0,  # ppm
            
            # وضعیت تجهیزات
            'pump_speed': 100.0,  # درصد
            'control_rod_position': 50.0,  # درصد خروج
        }
        
        # بافر برای ذخیره تاریخچه
        self.history = {key: [] for key in self.state.keys()}
        
    def _define_transients(self):
        """تعریف زمان و نوع گذراهای شبیه‌سازی شده"""
        self.transients = []
        
        # گذرای کاهش دبی پمپ (LOFA) در روز 7
        self.transients.append({
            'time': 7 * 86400,  # 7 روز به ثانیه
            'type': 'lofa',
            'duration': 1800,  # 30 دقیقه
            'severity': 0.7  # کاهش دبی به 70%
        })
        
        # گذرای افزایش توان در روز 14
        self.transients.append({
            'time': 14 * 86400,
            'type': 'power_ramp',
            'duration': 3600,
            'target_power': 1.1  # افزایش 10%
        })
        
        # گذرای نوسان فشار در روز 21
        self.transients.append({
            'time': 21 * 86400,
            'type': 'pressure_oscillation',
            'duration': 900,
            'amplitude': 0.5  # MPa نوسان
        })
        
        # گذرای ترکیبی در روز 25
        self.transients.append({
            'time': 25 * 86400,
            'type': 'combined',
            'duration': 1200
        })
        
    def _define_degradation_trends(self):
        """تعریف روندهای تخریب پارامترهای فیزیکی (رسوب، خوردگی، زبری)"""
        n = self.n_steps
        
        # ضریب زبری (افزایش تدریجی به علت خوردگی و اکسیداسیون)
        self.roughness_trend = generate_degradation_trend(
            initial=0.05, final=0.25, length=n, start_idx=0,
            degradation_type='exponential', noise=0.02
        )
        
        # مقاومت رسوب (افزایش تدریجی)
        self.fouling_trend = generate_degradation_trend(
            initial=0.0001, final=0.003, length=n, start_idx=0,
            degradation_type='linear', noise=0.01
        )
        
        # ضریب افت فشار (تغییرات پیچیده‌تر)
        self.k_loss_trend = generate_degradation_trend(
            initial=1.0, final=1.15, length=n, start_idx=0,
            degradation_type='saturation', noise=0.005
        )
        
    def _apply_transient(self, t, state):
        """اعمال شرایط گذرا بر اساس زمان"""
        for trans in self.transients:
            if abs(t - trans['time']) < trans['duration']:
                progress = (t - trans['time']) / trans['duration']
                
                if trans['type'] == 'lofa':
                    factor = 1 - (1 - trans['severity']) * min(1, progress)
                    state['mass_flow_rate'] = self.params.nominal_mass_flow * factor
                    state['volumetric_flow_rate'] = self.params.nominal_flow_rate * factor
                    
                elif trans['type'] == 'power_ramp':
                    power_factor = 1 + (trans['target_power'] - 1) * min(1, progress)
                    state['power'] = self.params.nominal_power * power_factor
                    
                elif trans['type'] == 'pressure_oscillation':
                    osc = trans['amplitude'] * np.sin(2 * np.pi * progress * 5)
                    state['pressure_primary'] = self.params.nominal_pressure + osc
                    
                elif trans['type'] == 'combined':
                    state['power'] = self.params.nominal_power * (1 + 0.05 * np.sin(progress * 2 * np.pi))
                    state['mass_flow_rate'] = self.params.nominal_mass_flow * (0.85 + 0.1 * np.sin(progress * 4 * np.pi))
        
        return state
    
    def _update_thermal_hydraulic(self, t, state):
        """
        محاسبه متغیرهای حرارتی-هیدرولیکی بر اساس:
        - توان
        - دبی
        - پارامترهای فیزیکی (زبری، رسوب)
        - شرایط گذرا
        """
        # محاسبه توان حرارتی بر اساس زمان (شامل نوسانات روزانه)
        daily_cycle = 1 + 0.05 * np.sin(2 * np.pi * t / 86400)
        state['power'] = self.params.nominal_power * daily_cycle
        
        # اثر زبری و رسوب بر افت فشار
        roughness_effect = self.roughness_trend[t] / 0.05  # نرمال‌شده
        fouling_effect = 1 + self.fouling_trend[t] * 500
        
        # افت فشار قلب
        dp_nominal = 0.15  # MPa
        state['pressure_drop_core'] = dp_nominal * roughness_effect * fouling_effect
        
        # فشار مدار اول (با در نظر گرفتن افت)
        state['pressure_primary'] = self.params.nominal_pressure - 0.5 * state['pressure_drop_core']
        
        # دمای ورودی (نوسان روزانه)
        inlet_cycle = 2 * np.sin(2 * np.pi * t / 86400)
        state['temp_inlet'] = self.params.nominal_inlet_temp + inlet_cycle
        
        # توان حرارتی به افزایش دما تبدیل می‌شود
        cp = 5.5  # kJ/kg.K (ظرفیت گرمایی آب)
        delta_t = state['power'] / (state['mass_flow_rate'] * cp)
        state['temp_outlet'] = state['temp_inlet'] + delta_t
        state['temp_core_avg'] = (state['temp_inlet'] + state['temp_outlet']) / 2
        
        # دمای داغ‌ترین کانال (Hot Channel Factor)
        hot_channel_factor = 1.3 + 0.1 * roughness_effect
        state['temp_hot_channel'] = state['temp_inlet'] + hot_channel_factor * delta_t
        
        # دمای غلاف سوخت (با مدل ساده انتقال حرارت)
        q_prime = state['power'] / (self.params.num_fuel_assemblies * self.params.fuel_rods_per_assembly)
        h_conv = 30 + 0.5 * state['mass_flow_rate']  # ضریب جابجایی
        state['temp_cladding_max'] = state['temp_hot_channel'] + q_prime / h_conv
        
        # DNBR (نسبت ایمنی جوش بحرانی)
        dnbr_nominal = self.params.min_dnbr + 0.5
        dnbr_degradation = 1 - 0.3 * fouling_effect
        state['dnbr'] = dnbr_nominal * dnbr_degradation * (state['mass_flow_rate'] / self.params.nominal_mass_flow)
        state['dnbr'] = max(1.0, min(2.5, state['dnbr']))
        
        # دبی جرمی با نویز
        state['mass_flow_rate'] = self.params.nominal_mass_flow * (0.95 + 0.1 * np.sin(t / 3600))
        
        return state
    
    def _update_neutronics(self, t, state):
        """محاسبه متغیرهای نوترونی و توان خطی"""
        # شار نوترونی محوری (توزیع سینوسی)
        axial_pos = np.sin(np.pi * (0.5 + 0.2 * np.sin(t / 86400)))
        state['neutron_flux_axial'] = axial_pos
        
        # شار نوترونی شعاعی
        radial_pos = 1 + 0.1 * np.cos(2 * np.pi * t / 3600)
        state['neutron_flux_radial'] = radial_pos
        
        # غلظت بور (تنظیم با توان)
        boron_target = 600 * (1 - (state['power'] - self.params.nominal_power) / self.params.nominal_power)
        state['boron_concentration'] = boron_target + np.random.normal(0, 5)
        
        return state
    
    def _update_equipment_state(self, t, state):
        """بروزرسانی وضعیت تجهیزات"""
        # سرعت پمپ (تنظیم بر اساس توان)
        state['pump_speed'] = 100 * (state['mass_flow_rate'] / self.params.nominal_mass_flow)
        
        # موقعیت میله کنترل (بر اساس توان و غلظت بور)
        rod_position = 50 * (state['power'] / self.params.nominal_power)
        state['control_rod_position'] = max(0, min(100, rod_position))
        
        return state
    
    def generate(self):
        """حلقه اصلی تولید داده"""
        print(f"شروع تولید داده سنتتیک راکتور برای {self.duration_days} روز")
        print(f"تعداد نقاط داده: {self.n_steps:,}")
        
        for t in range(self.n_steps):
            # کپی از وضعیت فعلی
            state = self.state.copy()
            
            # اعمال روند تخریب پارامترهای فیزیکی
            state['roughness_factor'] = self.roughness_trend[t]
            state['fouling_resistance'] = self.fouling_trend[t]
            state['pressure_drop_coefficient'] = self.k_loss_trend[t]
            
            # اعمال شرایط گذرا
            state = self._apply_transient(t, state)
            
            # محاسبات حرارتی-هیدرولیکی
            state = self._update_thermal_hydraulic(t, state)
            
            # محاسبات نوترونی
            state = self._update_neutronics(t, state)
            
            # وضعیت تجهیزات
            state = self._update_equipment_state(t, state)
            
            # اضافه کردن نویز به حسگرها
            noisy_state = {}
            for key, val in state.items():
                if 'temp' in key or 'pressure' in key:
                    noisy_state[key] = add_noise(val, noise_percent=0.5)
                elif 'flow' in key or 'power' in key:
                    noisy_state[key] = add_noise(val, noise_percent=1.0)
                else:
                    noisy_state[key] = val + np.random.normal(0, 0.01 * abs(val)) if abs(val) > 0 else val
            
            # ذخیره در تاریخچه
            for key, val in noisy_state.items():
                self.history[key].append(val)
        
        # تبدیل به دیتافریم
        df = pd.DataFrame(self.history)
        
        # اضافه کردن ستون زمان
        start_time = datetime(2024, 1, 1, 0, 0, 0)
        df['timestamp'] = [start_time + timedelta(seconds=i) for i in range(self.n_steps)]
        
        # شاخص‌های محاسباتی اضافی
        df['dnbr_margin'] = df['dnbr'] - self.params.min_dnbr
        df['temp_margin_to_limit'] = self.params.max_clad_temp - df['temp_cladding_max']
        df['time_to_dnb'] = np.maximum(0, (df['dnbr'] - 1) * 600)  # تخمین زمان تا DNB (ثانیه)
        df['degradation_alert'] = (df['roughness_factor'] > 0.15) | (df['fouling_resistance'] > 0.002)
        
        # محاسبه پارامترهای مخصوص EKF و DSS
        df['estimated_efpm'] = 0.95 * df['roughness_factor'] + 0.05 * np.random.randn(self.n_steps)
        df['fault_probability'] = 1 / (1 + np.exp(-10 * (df['roughness_factor'] - 0.12)))
        
        # رتبه‌بندی اقدامات DSS (ساختگی برای آموزش)
        df['dss_action_rank1'] = np.where(df['temp_cladding_max'] > 550, 'reduce_power_20%', 
                                   np.where(df['dnbr'] < 1.4, 'increase_flow_10%', 'continue_normal'))
        df['dss_safety_score'] = np.where(df['temp_cladding_max'] < 500, 5, 
                                   np.where(df['temp_cladding_max'] < 550, 3, 1))
        
        return df

# =====================================================
# اجرا و ذخیره داده‌ها
# =====================================================

if __name__ == "__main__":
    # ایجاد نمونه ژنراتور
    generator = SyntheticReactorDataGenerator(duration_days=30, time_step_sec=1)
    
    # تولید داده
    df = generator.generate()
    
    # نمایش اطلاعات
    print("\n" + "="*60)
    print("خلاصه داده‌های تولید شده:")
    print("="*60)
    print(f"تعداد رکوردها: {len(df):,}")
    print(f"بازه زمانی: {df['timestamp'].min()} تا {df['timestamp'].max()}")
    print(f"\nستون‌های موجود:\n{list(df.columns)}")
    
    # آمار توصیفی
    print("\nآمار توصیفی متغیرهای کلیدی:")
    key_cols = ['power', 'pressure_primary', 'temp_outlet', 'temp_cladding_max', 
                'dnbr', 'roughness_factor', 'fouling_resistance']
    print(df[key_cols].describe())
    
    # نمونه‌ای از داده‌های هشدار
    print("\nتعداد هشدارهای تخریب:", df['degradation_alert'].sum())
    print("توزیع توصیه DSS:")
    print(df['dss_action_rank1'].value_counts())
    
    # ذخیره در فایل‌های مختلف
    print("\nذخیره داده‌ها...")
    
    # ذخیره کامل (فشرده)
    df.to_parquet('reactor_synthetic_data_30days.parquet', compression='snappy')
    print("✓ ذخیره در format Parquet: reactor_synthetic_data_30days.parquet")
    
    # ذخیره نمونه برای تسک‌های ML (1% داده‌ها)
    sample_df = df.iloc[::3600, :]  # هر یک ساعت یک نمونه
    sample_df.to_csv('reactor_data_sample_hourly.csv', index=False)
    print("✓ نمونه ساعتی: reactor_data_sample_hourly.csv")
    
    # ذخیره متادیتا
    metadata = {
        'description': 'Synthetic PWR reactor data for Digital Twin training',
        'duration_days': 30,
        'time_step_sec': 1,
        'n_samples': len(df),
        'variables': list(df.columns),
        'transients_simulated': ['LOFA', 'Power Ramp', 'Pressure Oscillation', 'Combined'],
        'degradation_types': ['Roughness (exponential)', 'Fouling (linear)', 'K_loss (saturation)']
    }
    
    import json
    with open('reactor_data_metadata.json', 'w') as f:
        json.dump(metadata, f, indent=2)
    print("✓ متادیتا: reactor_data_metadata.json")
    
    print("\n✅ تولید داده با موفقیت کامل شد!")
    
    # نمایش نمودار ساده از روند تخریب
    import matplotlib.pyplot as plt
    
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # توان حرارتی
    axes[0,0].plot(df['timestamp'].iloc[:86400], df['power'].iloc[:86400], linewidth=0.5)
    axes[0,0].set_title('توان حرارتی راکتور (اولین روز)')
    axes[0,0].set_ylabel('MWth')
    
    # دمای غلاف سوخت
    axes[0,1].plot(df['timestamp'], df['temp_cladding_max'], linewidth=0.5, color='red')
    axes[0,1].axhline(y=620, color='black', linestyle='--', label='حد مجاز (620°C)')
    axes[0,1].set_title('دمای بیشینه غلاف سوخت در 30 روز')
    axes[0,1].set_ylabel('°C')
    axes[0,1].legend()
    
    # پارامترهای فیزیکی (تخریب)
    axes[1,0].plot(df['timestamp'], df['roughness_factor'], label='ضریب زبری', linewidth=0.5)
    axes[1,0].plot(df['timestamp'], df['fouling_resistance'] * 100, label='مقاومت رسوب ×100', linewidth=0.5)
    axes[1,0].set_title('روند تخریب پارامترهای فیزیکی')
    axes[1,0].set_ylabel('مقدار نرمال‌شده')
    axes[1,0].legend()
    
    # DNBR
    axes[1,1].plot(df['timestamp'], df['dnbr'], linewidth=0.5, color='green')
    axes[1,1].axhline(y=1.3, color='orange', linestyle='--', label='حد بحرانی DNBR')
    axes[1,1].set_title('نسبت ایمنی جوش بحرانی (DNBR)')
    axes[1,1].set_ylabel('DNBR')
    axes[1,1].legend()
    
    plt.tight_layout()
    plt.savefig('reactor_synthetic_data_plots.png', dpi=150)
    plt.show()
    
    print("\n📊 نمودارها در فایل reactor_synthetic_data_plots.png ذخیره شد.")