package com.hellennjerik.prepaid_electricity_monitor

data class RegisterRequest(
    val username: String,
    val password: String,
    val phone: String,
    val meter_number: String
)

data class LoginRequest(
    val username: String,
    val password: String
)

data class LoginResponse(
    val message: String,
    val user: UserInfo
)

data class UserInfo(
    val username: String,
    val meter_number: String
)

data class MeterStatus(
    val meter_number: String,
    val balance: Double,
    val kwh_used: Double,
    val status: String,
    val last_updated: String,
    val tier: String? = null,           // <--- NEW
    val rate_per_kwh: Double? = null    // <--- NEW
)

data class BuyTokenRequest(
    val meter_number: String,
    val amount: Int
)

data class BuyTokenResponse(
    val message: String,
    val token: String,
    val amount: Int,
    val units: Double
)

data class ApplyTokenRequest(
    val token_string: String,
    val meter_number: String
)

data class SimpleResponse(
    val message: String? = null,
    val error: String? = null
)

data class SolarStatus(
    val meter_number: String,
    val battery_percent: Double,
    val generation_kw: Double,
    val load_kw: Double,
    val solar_kwh_today: Double
)

data class PredictionResponse(
    val meter_number: String,
    val tokens: TokenPrediction,
    val solar: SolarPrediction
)

data class TokenPrediction(
    val balance_ksh: Double,
    val remaining_kwh: Double,
    val hours_until_empty: Double
)

data class SolarPrediction(
    val battery_percent: Double,
    val hours_until_empty: Double
)

data class ApplianceItem(
    val name: String,
    val frequency: String
)

data class RegisterAppliancesRequest(
    val meter_number: String,
    val appliances: List<ApplianceItem>
)

data class ApplianceAnalysisResponse(
    val meter_number: String,
    val total_kwh_per_day: Double,
    val estimated_monthly_cost: Double,
    val breakdown: List<ApplianceBreakdown>
)

data class ApplianceBreakdown(
    val appliance: String,
    val watts: Double,
    val frequency: String,
    val kwh_per_day: Double,
    val ksh_per_month: Double,
    val percent: Double
)

data class HistoryResponse(
    val meter_number: String,
    val tier: String,
    val rate_per_kwh: Double,
    val days: Int,
    val total_kwh: Double,
    val total_cost_ksh: Double,
    val avg_kwh_per_day: Double,
    val daily: List<DailyUsage>
)

data class DailyUsage(
    val date: String,
    val weekday: String,
    val kwh: Double,
    val cost_ksh: Double
)