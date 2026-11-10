package com.hellennjerik.prepaid_electricity_monitor

// ---- Accounts (server: /auth/*, /me) ----
data class SignUpRequest(
    val full_name: String,
    val email: String,
    val phone: String,
    val dob: String,
    val gender: String,          // female | male | other | prefer_not_to_say
    val account_type: String,    // home | work
    val meter_number: String,
    val password: String,
    val alert_threshold: Double = 100.0   // SMS alert when the balance falls to this many KSh
)

data class SignUpReply(val message: String? = null, val email: String? = null, val dev_code: String? = null)
data class VerifyRequest(val email: String, val code: String)
data class ResendRequest(val email: String)
data class SignInRequest(val email: String, val password: String)

data class MeterLinkInfo(val meter_number: String, val nickname: String? = null)

data class AccountProfile(
    val id: Int,
    val full_name: String,
    val email: String,
    val phone: String,
    val dob: String? = null,
    val gender: String? = null,
    val account_type: String? = null,
    val alert_threshold: Double? = null,
    val meters: List<MeterLinkInfo> = emptyList()
)

data class AuthResponse(val token: String, val user: AccountProfile)
data class AddMeterRequest(val meter_number: String, val nickname: String? = null)
data class UpdateProfileRequest(val full_name: String? = null, val phone: String? = null, val alert_threshold: Double? = null)

// ---- Reports, planner, alerts ----
data class ReportBucket(val label: String, val kwh: Double, val cost_ksh: Double, val days: Int, val partial: Boolean)

data class ReportResponse(
    val meter_number: String,
    val period: String,
    val tier: String,
    val rate_per_kwh: Double,
    val total_kwh: Double,
    val total_cost_ksh: Double,
    val avg_kwh_per_day: Double,
    val buckets: List<ReportBucket>
)

data class PlannerResponse(
    val meter_number: String,
    val tier: String,
    val rate_per_kwh: Double,
    val amount_ksh: Double,
    val units_kwh: Double,
    val energy_ksh: Double,
    val levies_and_taxes_ksh: Double,
    val levies_percent: Int,
    val avg_daily_kwh: Double? = null,
    val days_it_lasts: Double? = null,
    val days_with_current_balance: Double? = null,
    val basis: String? = null
)

data class AlertItem(
    val kind: String,
    val level: String? = null,
    val message: String,
    val hours_left: Double? = null,
    val pct_change: Int? = null
)

data class AlertHistoryItem(val kind: String, val message: String, val sent_at: String)
data class AlertsResponse(val meter_number: String, val active: List<AlertItem>, val history: List<AlertHistoryItem>)