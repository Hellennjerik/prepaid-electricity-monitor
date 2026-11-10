package com.hellennjerik.prepaid_electricity_monitor

// (The old RegisterRequest / LoginRequest / LoginResponse / UserInfo classes are gone:
//  accounts now use the classes in AuthModels.kt.)

data class MeterStatus(
    val meter_number: String,
    val balance: Double,
    val kwh_used: Double,
    val status: String,
    val last_updated: String,
    val tier: String? = null,
    val rate_per_kwh: Double? = null,
    val history_days: Int? = null
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
    val solar: SolarPrediction,
    val billing: BillingInfo? = null
)

data class BillingInfo(
    val tier: String? = null,
    val rate_per_kwh: Double? = null,
    val avg_daily_kwh: Double? = null,
    val avg_daily_spend_ksh: Double? = null,
    val basis: String? = null
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
    val frequency: String,
    val watts: Double? = null,
    val hours_per_day: Double? = null
)

data class RegisterAppliancesRequest(
    val meter_number: String,
    val appliances: List<ApplianceItem>
)

data class ApplianceAnalysisResponse(
    val meter_number: String,
    val tier: String? = null,
    val rate_per_kwh: Double? = null,
    val total_kwh_per_day: Double,
    val estimated_monthly_cost: Double,
    val breakdown: List<ApplianceBreakdown>
)

data class ApplianceBreakdown(
    val appliance: String,
    val label: String? = null,
    val watts: Double,
    val frequency: String,
    val hours_per_day: Double? = null,
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

// ---- appliance tick-list ----
data class CatalogItem(
    val key: String,
    val label: String,
    val category: String,
    val watts: Double,
    val hours_per_day: Double,
    val tip: String? = null
)

data class CatalogResponse(val appliances: List<CatalogItem>)
data class SavedAppliancesResponse(val meter_number: String, val keys: List<String>)

// ---- weather for solar homes ----
data class WeatherDay(
    val label: String,
    val outlook: String,
    val emoji: String,
    val sunshine_hours: Double,
    val solar: String
)

data class WeatherResponse(
    val available: Boolean = false,
    val days: List<WeatherDay> = emptyList(),
    val advice: String? = null
)

data class FeedbackRequest(
    val category: String,
    val message: String
)
