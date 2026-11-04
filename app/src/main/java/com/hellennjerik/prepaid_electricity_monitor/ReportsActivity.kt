package com.hellennjerik.prepaid_electricity_monitor

import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

class ReportsActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_reports)

        val meterNumber = intent.getStringExtra("METER_NUMBER") ?: "M001"
        val tvSummary = findViewById<TextView>(R.id.tvReportSummary)
        val chartContainer = findViewById<LinearLayout>(R.id.chartContainer)

        // Fetch the history from the API
        lifecycleScope.launch {
            try {
                val response = ApiClient.api.getHistory(meterNumber, 7)
                if (response.isSuccessful) {
                    val data = response.body()
                    if (data != null) {
                        // Update Summary
                        tvSummary.text = "Total: ${data.total_kwh} kWh\nCost: KSh ${data.total_cost_ksh}\nAvg: ${data.avg_kwh_per_day} kWh/day"

                        // Clear old bars
                        chartContainer.removeAllViews()

                        // Find the max kWh to scale the bars correctly
                        val maxKwh = data.daily.maxOfOrNull { it.kwh } ?: 1.0
                        val maxBarHeightPx = 600 // Max height in pixels

                        // Draw a bar for each day
                        for (day in data.daily) {
                            val dayLayout = LinearLayout(this@ReportsActivity)
                            dayLayout.orientation = LinearLayout.VERTICAL
                            dayLayout.gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
                            val params = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1f)
                            dayLayout.layoutParams = params

                            val ratio = (day.kwh / maxKwh).toFloat()
                            val heightPx = (maxBarHeightPx * ratio).toInt().coerceAtLeast(20)

                            // The colored Bar
                            val bar = TextView(this@ReportsActivity)
                            val barParams = LinearLayout.LayoutParams(60, heightPx)
                            barParams.bottomMargin = 8
                            bar.layoutParams = barParams
                            bar.setBackgroundColor(Color.parseColor("#0EA5E9")) // Neon Blue

                            // The Weekday Label
                            val label = TextView(this@ReportsActivity)
                            label.text = day.weekday
                            label.setTextColor(Color.parseColor("#94A3B8"))
                            label.textSize = 12f

                            dayLayout.addView(bar)
                            dayLayout.addView(label)
                            chartContainer.addView(dayLayout)
                        }
                    }
                }
            } catch (e: Exception) {
                Toast.makeText(this@ReportsActivity, "Error: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }
}