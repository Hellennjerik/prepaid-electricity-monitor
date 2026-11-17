package com.hellennjerik.prepaid_electricity_monitor

import android.graphics.Color
import android.os.Bundle
import android.view.View
import android.widget.EditText
import android.widget.PopupMenu
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.bottomnavigation.BottomNavigationView
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

class DashboardActivity : AppCompatActivity() {

    private lateinit var tvMeterChip: TextView
    private lateinit var cardAlert: View
    private lateinit var tvAlert: TextView
    private lateinit var viewBellDot: View
    private lateinit var tvUnits: TextView
    private lateinit var tvBalance: TextView
    private lateinit var tvRate: TextView
    private lateinit var tvTariffBadge: TextView
    private lateinit var tvTimeLeft: TextView
    private lateinit var tvAvgDaily: TextView
    private lateinit var tvUpdated: TextView
    private lateinit var tvSolarSource: TextView
    private lateinit var tvSolarDetails: TextView
    private lateinit var tvSolarTip: TextView
    private lateinit var pbBattery: ProgressBar
    private lateinit var solarGroup: View
    private lateinit var tvBatteryPct: TextView
    private lateinit var tvSolarGen: TextView
    private lateinit var tvSolarLoad: TextView
    private lateinit var tvSolarToday: TextView
    private lateinit var tvSolarWeather: TextView
    private lateinit var viewWeatherDivider: View
    private lateinit var mixSolar: View
    private lateinit var mixBattery: View
    private lateinit var mixGrid: View
    private lateinit var tvMixLegend: TextView
    private lateinit var tvMixNote: TextView

    private var newMeterNotice = ""      // shown while a meter has under a week of history
    private var weatherText = ""          // refreshed every 10 minutes for solar homes
    private var lastWeatherAt = 0L

    /** The meter shown on every screen. Chosen from the chip at the top of this screen. */
    private val meterNumber: String get() = SessionStore.currentMeter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_dashboard)

        tvMeterChip = findViewById(R.id.tvMeterChip)
        cardAlert = findViewById(R.id.cardAlert)
        tvAlert = findViewById(R.id.tvAlert)
        viewBellDot = findViewById(R.id.viewBellDot)
        tvUnits = findViewById(R.id.tvUnits)
        tvBalance = findViewById(R.id.tvBalance)
        tvRate = findViewById(R.id.tvRate)
        tvTariffBadge = findViewById(R.id.tvTariffBadge)
        tvTimeLeft = findViewById(R.id.tvTimeLeft)
        tvAvgDaily = findViewById(R.id.tvAvgDaily)
        tvUpdated = findViewById(R.id.tvUpdated)
        tvSolarSource = findViewById(R.id.tvSolarSource)
        tvSolarDetails = findViewById(R.id.tvSolarDetails)
        tvSolarTip = findViewById(R.id.tvSolarTip)
        pbBattery = findViewById(R.id.pbBattery)
        solarGroup = findViewById(R.id.solarGroup)
        tvBatteryPct = findViewById(R.id.tvBatteryPct)
        tvSolarGen = findViewById(R.id.tvSolarGen)
        tvSolarLoad = findViewById(R.id.tvSolarLoad)
        tvSolarToday = findViewById(R.id.tvSolarToday)
        tvSolarWeather = findViewById(R.id.tvSolarWeather)
        viewWeatherDivider = findViewById(R.id.viewWeatherDivider)
        mixSolar = findViewById(R.id.mixSolar)
        mixBattery = findViewById(R.id.mixBattery)
        mixGrid = findViewById(R.id.mixGrid)
        tvMixLegend = findViewById(R.id.tvMixLegend)
        tvMixNote = findViewById(R.id.tvMixNote)

        // Greeting and avatar
        val firstName = SessionStore.fullName.trim().substringBefore(' ').ifEmpty { "there" }
        val hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
        val timeOfDay = when { hour < 12 -> "Good morning"; hour < 17 -> "Good afternoon"; else -> "Good evening" }
        findViewById<TextView>(R.id.tvGreeting).text = "$timeOfDay, $firstName 👋"
        findViewById<TextView>(R.id.tvAvatar).text = firstName.take(1).uppercase()

        setupBottomNav(R.id.nav_meter)
        updateMeterChip()
        tvMeterChip.setOnClickListener { showMeterPicker() }

        // Shortcut grid
        findViewById<View>(R.id.tileRead).setOnClickListener {
            tvUpdated.text = "Reading meter..."
            lifecycleScope.launch { refreshDashboard() }
        }
        findViewById<View>(R.id.tileTopUp).setOnClickListener { showTopUpDialog() }
        findViewById<View>(R.id.tilePlan).setOnClickListener { showPlanDialog() }
        findViewById<View>(R.id.tileSolar).setOnClickListener {
            val scroll = findViewById<ScrollView>(R.id.scrollMain)
            val card = findViewById<View>(R.id.cardSolar)
            scroll.post { scroll.smoothScrollTo(0, card.top) }
        }
        val nav = findViewById<BottomNavigationView>(R.id.bottom_nav)
        findViewById<View>(R.id.tileAppliances).setOnClickListener { nav.selectedItemId = R.id.nav_appliances }
        findViewById<View>(R.id.tileReports).setOnClickListener { nav.selectedItemId = R.id.nav_reports }
        findViewById<View>(R.id.tileLearn).setOnClickListener { nav.selectedItemId = R.id.nav_learn }
        findViewById<View>(R.id.tileAlerts).setOnClickListener { showAlertsDialog() }
        findViewById<View>(R.id.btnBell).setOnClickListener { showAlertsDialog() }

        startLiveUpdates()
    }

    // ---------- meter picker ----------

    private fun updateMeterChip() {
        val current = SessionStore.meters.firstOrNull { it.meter_number == meterNumber }
        val name = current?.nickname?.takeIf { it.isNotBlank() }
        tvMeterChip.text = (if (name != null) "$name · $meterNumber" else meterNumber) + " ▾"
    }

    private fun showMeterPicker() {
        val meters = SessionStore.meters
        if (meters.size < 2) {
            toast("Add more meters from the Profile tab")
            return
        }
        val popup = PopupMenu(this, tvMeterChip)
        meters.forEachIndexed { i, m -> popup.menu.add(0, i, i, (m.nickname?.let { "$it · " } ?: "") + m.meter_number) }
        popup.setOnMenuItemClickListener { item ->
            SessionStore.currentMeter = meters[item.itemId].meter_number
            updateMeterChip()
            tvUpdated.text = "Loading..."
            lifecycleScope.launch { refreshDashboard() }
            true
        }
        popup.show()
    }

    // ---------- live data ----------

    // One polling loop, only while the screen is visible, started exactly once.
    private fun startLiveUpdates() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    refreshDashboard()
                    delay(5000)
                }
            }
        }
    }

    private suspend fun refreshDashboard() {
        val meter = meterNumber
        if (meter.isEmpty()) return
        try {
            // --- meter: balance, tier and price come from the server ---
            val meterResponse = ApiClient.api.getMeterStatus(meter)
            if (meterResponse.isSuccessful) {
                val m = meterResponse.body()
                val balanceKsh = m?.balance ?: 0.0
                val rate = m?.rate_per_kwh ?: 0.0
                tvUnits.text = String.format(Locale.US, "%.1f", if (rate > 0) balanceKsh / rate else 0.0)
                tvBalance.text = String.format(Locale.US, "%,.0f", balanceKsh)
                tvTariffBadge.text = m?.tier ?: "--"
                tvRate.text = if (rate > 0) "KSh ${String.format(Locale.US, "%.2f", rate)} per unit" else ""
                newMeterNotice = if ((m?.history_days ?: 99) < 7)
                    "🆕 This meter is new. Record each top-up and the app will learn your usage. Predictions get better after about a week."
                else ""
            }

            // --- solar or grid only ---
            val solarResponse = ApiClient.api.getSolarStatus(meter)
            val solarBody = if (solarResponse.isSuccessful) solarResponse.body() else null
            showSolar(solarBody)
            refreshWeather(solarBody != null && hasSolar(solarBody))

            // --- prediction ---
            val predictResponse = ApiClient.api.getPrediction(meter)
            if (predictResponse.isSuccessful) {
                val p = predictResponse.body()
                showPrediction(p?.tokens?.hours_until_empty ?: 0.0, p?.billing)
            }

            // --- alerts ---
            val alertResponse = ApiClient.api.getAlerts(meter)
            val active = if (alertResponse.isSuccessful) alertResponse.body()?.active.orEmpty() else emptyList()
            viewBellDot.visibility = if (active.isEmpty()) View.GONE else View.VISIBLE
            val notices = listOfNotNull(newMeterNotice.ifEmpty { null }) +
                    active.map { (if (it.kind == "low_credit") "⚠️ " else "📈 ") + it.message }
            if (notices.isEmpty()) {
                cardAlert.visibility = View.GONE
            } else {
                tvAlert.text = notices.joinToString("\n\n")
                cardAlert.visibility = View.VISIBLE
            }

            tvUpdated.text = "Meter updated " + SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
        } catch (e: Exception) {
            // Network down: keep showing the last numbers instead of blanking the screen.
            tvUpdated.text = "Offline - showing the last data we received"
        }
    }

    private fun showPrediction(hours: Double, billing: BillingInfo?) {
        val green = Color.parseColor("#34D399")
        val gold = Color.parseColor("#F5B301")
        val red = Color.parseColor("#F87171")
        when {
            hours >= 48 -> { tvTimeLeft.text = "~${String.format(Locale.US, "%.0f", hours / 24)} days"; tvTimeLeft.setTextColor(if (hours >= 72) green else gold) }
            hours >= 1 -> { tvTimeLeft.text = "~${String.format(Locale.US, "%.0f", hours)} hours"; tvTimeLeft.setTextColor(if (hours >= 24) gold else red) }
            else -> { tvTimeLeft.text = "< 1 hour"; tvTimeLeft.setTextColor(red) }
        }
        val daily = billing?.avg_daily_kwh
        tvAvgDaily.text = if (daily != null) String.format(Locale.US, "%.1f", daily) else "--"
    }

    private fun hasSolar(s: SolarStatus) = s.battery_percent > 0.0 || s.generation_kw > 0.0 || s.solar_kwh_today > 0.0

    // Forecast for solar homes only, at most every 10 minutes. If it is unavailable the line just stays empty.
    private suspend fun refreshWeather(isSolarHome: Boolean) {
        if (!isSolarHome) { weatherText = ""; return }
        if (System.currentTimeMillis() - lastWeatherAt < 10 * 60 * 1000 && weatherText.isNotEmpty()) return
        try {
            val r = ApiClient.api.getWeather(meterNumber)
            val w = r.body()
            lastWeatherAt = System.currentTimeMillis()
            weatherText = if (r.isSuccessful && w != null && w.available) {
                val outlook = w.days.take(3).joinToString("   ") { "${it.emoji} ${it.label}: ${it.outlook} (${String.format(Locale.US, "%.0f", it.sunshine_hours)}h sun)" }
                "$outlook\n${w.advice ?: ""}"
            } else ""
            showWeather()
        } catch (e: Exception) {
            // no internet for the forecast: keep going without it
        }
    }

    /** Stacked bar: how much of the home's load is met by solar, the battery and the Kenya Power grid. */
    private fun showEnergyMix(flow: SolarFlow?) {
        fun weight(v: View, w: Float) {
            val p = v.layoutParams as android.widget.LinearLayout.LayoutParams
            p.weight = w
            v.layoutParams = p
        }
        if (flow == null || (flow.solar_pct + flow.battery_pct + flow.grid_pct) == 0) {
            weight(mixSolar, 0f); weight(mixBattery, 0f); weight(mixGrid, 0f)
            tvMixLegend.text = ""
            tvMixNote.text = flow?.note ?: ""
            return
        }
        weight(mixSolar, flow.solar_pct.toFloat())
        weight(mixBattery, flow.battery_pct.toFloat())
        weight(mixGrid, flow.grid_pct.toFloat())
        tvMixLegend.text = "☀️ Solar ${flow.solar_pct}%    🔋 Battery ${flow.battery_pct}%    ⚡ Grid ${flow.grid_pct}%"
        tvMixNote.text = flow.note
    }

    private fun showWeather() {
        val show = weatherText.isNotEmpty() && solarGroup.visibility == View.VISIBLE
        tvSolarWeather.text = weatherText
        tvSolarWeather.visibility = if (show) View.VISIBLE else View.GONE
        viewWeatherDivider.visibility = if (show) View.VISIBLE else View.GONE
    }

    private fun showSolar(solar: SolarStatus?) {
        val battery = solar?.battery_percent ?: 0.0
        val generation = solar?.generation_kw ?: 0.0
        val load = solar?.load_kw ?: 0.0
        val madeToday = solar?.solar_kwh_today ?: 0.0
        val hasSolar = solar != null && (battery > 0.0 || generation > 0.0 || madeToday > 0.0)

        if (!hasSolar) {
            tvSolarSource.text = "Grid only"
            solarGroup.visibility = View.GONE
            tvSolarDetails.visibility = View.VISIBLE
            tvSolarDetails.text = "This home runs on Kenya Power only. No solar system is linked to this meter."
            showWeather()
            return
        }
        tvSolarDetails.visibility = View.GONE
        solarGroup.visibility = View.VISIBLE
        tvSolarSource.text = when {
            generation > load && generation > 0 -> "☀️ Solar is covering your home"
            generation > 0 -> "☀️ Solar is helping power your home"
            battery > 0 -> "🔋 Running on battery"
            else -> "Running on the grid"
        }
        val pct = battery.toInt().coerceIn(0, 100)
        pbBattery.progress = pct
        pbBattery.progressTintList = android.content.res.ColorStateList.valueOf(
            Color.parseColor(if (pct > 60) "#34D399" else if (pct > 30) "#F5B301" else "#F87171"))
        tvBatteryPct.text = "$pct%"
        tvSolarGen.text = String.format(Locale.US, "%.1f kW", generation)
        tvSolarLoad.text = String.format(Locale.US, "%.1f kW", load)
        tvSolarToday.text = String.format(Locale.US, "%.1f kWh", madeToday)
        showEnergyMix(solar?.flow)
        tvSolarTip.text = if (generation > 0.3) "💡 Good sun right now: a good time to iron, wash or run the water pump."
        else "💡 Low sun right now: go easy on heavy appliances to save the battery."
        showWeather()
    }

    // ---------- alerts ----------

    private fun showAlertsDialog() {
        lifecycleScope.launch {
            try {
                val r = ApiClient.api.getAlerts(meterNumber)
                val data = r.body()
                if (!r.isSuccessful || data == null) return@launch toast(r.errorText("Could not load alerts"))
                val text = StringBuilder()
                if (data.active.isEmpty()) text.append("✅ No warnings right now.") else {
                    text.append("Right now:\n")
                    data.active.forEach { text.append("• ${it.message}\n") }
                }
                if (data.history.isNotEmpty()) {
                    text.append("\nRecent SMS alerts:\n")
                    data.history.take(5).forEach { text.append("• ${it.sent_at.take(16)}  ${it.message}\n") }
                }
                AlertDialog.Builder(this@DashboardActivity).setTitle("🔔 Alerts").setMessage(text.toString())
                    .setPositiveButton("OK") { d, _ -> d.dismiss() }.show()
            } catch (e: Exception) {
                toast("Cannot reach the server")
            }
        }
    }

    // ---------- "Will it last?" ----------

    private fun showPlanDialog() {
        val input = EditText(this)
        input.hint = "Amount in KSh (e.g. 1000)"
        input.inputType = android.text.InputType.TYPE_CLASS_NUMBER
        AlertDialog.Builder(this)
            .setTitle("Will it last?")
            .setMessage("Enter an amount to see how many units it buys and how long it should last.")
            .setView(input)
            .setPositiveButton("Check") { d, _ ->
                val amount = input.text.toString().toDoubleOrNull()
                d.dismiss()
                if (amount != null) lifecycleScope.launch { showPlanResult(amount) }
            }
            .setNegativeButton("Cancel") { d, _ -> d.dismiss() }
            .show()
    }

    private suspend fun showPlanResult(amount: Double) {
        try {
            val r = ApiClient.api.getPlanner(meterNumber, amount)
            val p = r.body()
            if (r.isSuccessful && p != null) {
                val lasts = p.days_it_lasts?.let { "It should last about ${String.format(Locale.US, "%.1f", it)} days at your usual use." }
                    ?: "Not enough history yet to say how long it lasts."
                AlertDialog.Builder(this)
                    .setTitle("KSh ${String.format(Locale.US, "%,.0f", p.amount_ksh)} buys about ${p.units_kwh} units")
                    .setMessage("Tariff band: ${p.tier} (KSh ${p.rate_per_kwh} per unit)\n\n" +
                            "• Electricity: KSh ${String.format(Locale.US, "%,.0f", p.energy_ksh)}\n" +
                            "• Levies and taxes: KSh ${String.format(Locale.US, "%,.0f", p.levies_and_taxes_ksh)} (${p.levies_percent}%)\n\n$lasts")
                    .setPositiveButton("OK") { d, _ -> d.dismiss() }
                    .show()
            } else {
                toast(r.errorText("Enter an amount between KSh 10 and KSh 100,000"))
            }
        } catch (e: Exception) {
            toast("Cannot reach the server")
        }
    }

    // ---------- record a top-up ----------

    private fun showTopUpDialog() {
        val input = EditText(this)
        input.hint = "Amount in KSh (e.g. 1000)"
        input.inputType = android.text.InputType.TYPE_CLASS_NUMBER
        AlertDialog.Builder(this)
            .setTitle("Record a top-up")
            .setMessage("Bought tokens through M-Pesa, *977# or the Kenya Power app? Enter the amount so the app can update your units.")
            .setView(input)
            .setPositiveButton("Record") { dialog, _ ->
                val amount = input.text.toString().toIntOrNull()
                dialog.dismiss()
                if (amount != null && amount > 0) executeTokenPurchase(amount)
            }
            .setNegativeButton("Cancel") { dialog, _ -> dialog.dismiss() }
            .show()
    }

    private fun executeTokenPurchase(amount: Int) {
        lifecycleScope.launch {
            try {
                val response = ApiClient.api.buyToken(BuyTokenRequest(meterNumber, amount))
                if (response.isSuccessful) {
                    val data = response.body()
                    AlertDialog.Builder(this@DashboardActivity)
                        .setTitle("Top-up recorded")
                        .setMessage("Amount: KSh ${data?.amount}\n\nUnits added: ${data?.units} kWh\n\nToken: ${data?.token}")
                        .setPositiveButton("OK") { d, _ -> d.dismiss() }
                        .show()
                    refreshDashboard()
                } else {
                    toast(response.errorText("Could not record the top-up"))
                }
            } catch (e: Exception) {
                toast("Error: ${e.message}")
            }
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
