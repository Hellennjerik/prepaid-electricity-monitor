package com.hellennjerik.prepaid_electricity_monitor

import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButtonToggleGroup
import kotlinx.coroutines.launch
import java.util.Locale

class ReportsActivity : AppCompatActivity() {

    private lateinit var chartContainer: LinearLayout
    private var period = "daily"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_reports)
        setupBottomNav(R.id.nav_reports)

        chartContainer = findViewById(R.id.chartContainer)
        findViewById<TextView>(R.id.tvReportMeter).text = "Meter ${SessionStore.currentMeter}"

        findViewById<MaterialButtonToggleGroup>(R.id.toggleGroup).addOnButtonCheckedListener { _, id, checked ->
            if (!checked) return@addOnButtonCheckedListener
            period = when (id) {
                R.id.btnWeekly -> "weekly"
                R.id.btnMonthly -> "monthly"
                else -> "daily"
            }
            load()
        }
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            try {
                val response = ApiClient.api.getReport(SessionStore.currentMeter, period)
                val data = response.body()
                if (response.isSuccessful && data != null) draw(data) else toast(response.errorText("Could not load the report"))
            } catch (e: Exception) {
                toast("Cannot reach the server. Check your connection.")
            }
        }
    }

    private fun draw(data: ReportResponse) {
        findViewById<TextView>(R.id.tvTotalKwh).text = String.format(Locale.US, "%.1f", data.total_kwh)
        findViewById<TextView>(R.id.tvTotalCost).text = String.format(Locale.US, "%,.0f", data.total_cost_ksh)
        findViewById<TextView>(R.id.tvAvg).text = String.format(Locale.US, "%.1f", data.avg_kwh_per_day)

        val shown = if (data.period == "daily") data.buckets.takeLast(7) else data.buckets
        val note = StringBuilder("Cost uses your current rate of KSh ${data.rate_per_kwh} per unit (${data.tier}).")
        if (shown.any { it.partial }) note.append(" Lighter bars cover fewer days than a full ${if (period == "weekly") "week" else "month"}.")
        findViewById<TextView>(R.id.tvChartNote).text = note

        chartContainer.removeAllViews()
        val maxKwh = shown.maxOfOrNull { it.kwh }?.takeIf { it > 0 } ?: 1.0
        val maxBarPx = dp(160)

        for (b in shown) {
            val column = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1f)
            }
            val value = TextView(this).apply {
                text = String.format(Locale.US, "%.1f", b.kwh)
                textSize = 12f
                setTextColor(Color.parseColor("#94A3B8"))
                gravity = Gravity.CENTER
            }
            val bar = TextView(this).apply {
                layoutParams = LinearLayout.LayoutParams(dp(26), ((b.kwh / maxKwh) * maxBarPx).toInt().coerceAtLeast(dp(4))).apply { bottomMargin = dp(6); topMargin = dp(4) }
                setBackgroundColor(Color.parseColor(if (b.partial) "#FCD34D" else "#F5B301"))
            }
            val label = TextView(this).apply {
                text = b.label.replace(" ", "\n")
                textSize = 12f
                setTextColor(Color.parseColor("#94A3B8"))
                gravity = Gravity.CENTER
            }
            column.addView(value)
            column.addView(bar)
            column.addView(label)
            chartContainer.addView(column)
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}