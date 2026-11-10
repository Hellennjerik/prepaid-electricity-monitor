package com.hellennjerik.prepaid_electricity_monitor

import android.graphics.Color
import android.content.res.ColorStateList
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import java.util.Locale

class ApplianceActivity : AppCompatActivity() {

    private val boxes = linkedMapOf<String, CheckBox>()       // appliance key -> its checkbox
    private var catalog: List<CatalogItem> = emptyList()
    private val meter: String get() = SessionStore.currentMeter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_appliances)
        setupBottomNav(R.id.nav_appliances)
        findViewById<TextView>(R.id.tvApplMeter).text = "Meter $meter"

        val btnSave = findViewById<Button>(R.id.btnSaveAppliances)
        btnSave.setOnClickListener {
            val chosen = catalog.filter { boxes[it.key]?.isChecked == true }
            if (chosen.isEmpty()) return@setOnClickListener toast("Tick at least one appliance")
            btnSave.isEnabled = false
            btnSave.text = "Analysing..."
            lifecycleScope.launch {
                try {
                    val items = chosen.map { ApplianceItem(it.key, "typical", it.watts, it.hours_per_day) }
                    val saved = ApiClient.api.registerAppliances(RegisterAppliancesRequest(meter, items))
                    if (saved.isSuccessful) {
                        showAnalysis()
                        findViewById<ScrollView>(R.id.scrollAppliances).smoothScrollTo(0, 0)
                    } else toast(saved.errorText("Could not save your appliances"))
                } catch (e: Exception) {
                    toast("Cannot reach the server. Check your connection.")
                } finally {
                    btnSave.isEnabled = true
                    btnSave.text = "Analyse my usage"
                }
            }
        }

        lifecycleScope.launch {
            try {
                val c = ApiClient.api.getCatalog()
                catalog = c.body()?.appliances.orEmpty()
                buildChecklist()
                val saved = ApiClient.api.getSavedAppliances(meter).body()?.keys.orEmpty()
                saved.forEach { boxes[it]?.isChecked = true }
                if (saved.isNotEmpty()) showAnalysis()      // show last time's result straight away
            } catch (e: Exception) {
                toast("Cannot reach the server. Check your connection.")
            }
        }
    }

    private fun buildChecklist() {
        val list = findViewById<LinearLayout>(R.id.catalogList)
        list.removeAllViews()
        var lastCategory = ""
        for (item in catalog) {
            if (item.category != lastCategory) {
                lastCategory = item.category
                list.addView(TextView(this).apply {
                    text = item.category.uppercase()
                    textSize = 12f
                    setTextColor(Color.parseColor("#94A3B8"))
                    letterSpacing = 0.08f
                    setPadding(0, dp(16), 0, dp(4))
                })
            }
            val box = CheckBox(this).apply {
                text = "${item.label}  ·  ${item.watts.toInt()} W"
                textSize = 16f
                minHeight = dp(48)
                setTextColor(resources.getColor(R.color.text_primary, theme))
                buttonTintList = ColorStateList.valueOf(Color.parseColor("#F5B301"))
            }
            boxes[item.key] = box
            list.addView(box)
        }
    }

    private suspend fun showAnalysis() {
        try {
            val r = ApiClient.api.analyzeAppliances(meter)
            val data = r.body() ?: return
            if (!r.isSuccessful) return

            findViewById<View>(R.id.cardResult).visibility = View.VISIBLE
            findViewById<TextView>(R.id.tvResultTotal).text = "KSh ${String.format(Locale.US, "%,.0f", data.estimated_monthly_cost)}"
            val top = data.breakdown.firstOrNull()
            val topTwo = data.breakdown.take(2).sumOf { it.percent }
            findViewById<TextView>(R.id.tvResultSummary).text = if (top != null)
                "${top.label ?: top.appliance} uses the most (${top.percent.toInt()}%). Your top two make up ${topTwo.toInt()}% of the bill."
            else ""

            val list = findViewById<LinearLayout>(R.id.resultList)
            list.removeAllViews()
            for (item in data.breakdown) {
                list.addView(TextView(this).apply {
                    text = "${item.label ?: item.appliance}   KSh ${String.format(Locale.US, "%,.0f", item.ksh_per_month)} a month  ·  ${item.percent.toInt()}%"
                    textSize = 14f
                    setTextColor(resources.getColor(R.color.text_primary, theme))
                    setPadding(0, dp(8), 0, dp(2))
                })
                list.addView(ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
                    max = 100
                    progress = item.percent.toInt().coerceAtLeast(1)
                    progressTintList = ColorStateList.valueOf(Color.parseColor(if (item === top) "#F59E0B" else "#F5B301"))
                })
            }

            val tip = catalog.firstOrNull { it.key == top?.appliance }?.tip
            findViewById<TextView>(R.id.tvResultTip).text = if (tip != null) "💡 $tip" else ""
            findViewById<TextView>(R.id.tvResultNote).text =
                "An estimate from typical use, priced at your ${data.tier ?: ""} rate (KSh ${data.rate_per_kwh ?: ""} per unit). Real use varies with your household."
        } catch (e: Exception) {
            toast("Cannot reach the server. Check your connection.")
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}