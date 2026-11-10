package com.hellennjerik.prepaid_electricity_monitor

import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.card.MaterialCardView

/** Plain explainers: no server needed, just text. Tap a card to open or close it. */
class LearnActivity : AppCompatActivity() {

    private val topics = listOf(
        "⚡ How tariff bands (DC1, DC2, DC3) work" to
                "Kenya Power groups homes by how much electricity they use in a month:\n\n" +
                "• DC1: up to 30 kWh\n• DC2: 31 to 100 kWh\n• DC3: above 100 kWh\n\n" +
                "Your band comes from your average over the last three months, so one unusually heavy month does not move you straight away. " +
                "This app applies the same idea to your meter's history and shows the matching price per unit on the Home screen.\n\n" +
                "Prices and rules can change, so treat the figures here as estimates and check your own token receipt.",

        "💰 Where does my money go?" to
                "Not all of a token purchase turns into units. A purchase is split into:\n\n" +
                "• The energy itself\n• Fuel energy charge\n• Foreign exchange adjustment (FERFA)\n• Water resources levy\n" +
                "• Energy regulator (ERC) levy\n• Rural electrification (REP) levy, about 5%\n• VAT at 16%\n\n" +
                "In the real token purchases we studied, roughly a third (32 to 36%) went to charges other than the energy. " +
                "Use \"Will it last?\" on the Home screen to see the split before you buy.",

        "🤔 Why do I get fewer units than I expected?" to
                "Because of the charges above, KSh 1,000 buys fewer units than 1,000 divided by the energy price. " +
                "The higher band you are in, the more each unit costs, so the same money buys fewer units in DC3 than in DC1.",

        "🔋 How does the app know my balance?" to
                "The app starts from your recorded top-ups and uses your usual daily use to work out what is left. " +
                "It is an estimate, and your real meter always has the final word. " +
                "Tap \"Read live meter\" to refresh, and record each new top-up so the estimate stays accurate.",

        "⏳ How is \"time left\" worked out?" to
                "We take your balance and divide it by what you normally spend each day, using your last two weeks of use. " +
                "If you suddenly use much more or less than usual, the estimate will catch up over a few days.",

        "📩 Why did I get an SMS?" to
                "You get a text when your credit is predicted to run out in under 24 hours, or when you are using at least 30% more than usual. " +
                "Each kind of alert is sent at most once every 12 hours. The text goes to the phone number on your Profile.",

        "💡 Simple ways to spend less" to
                "• Shorter showers, or a solar water heater\n• Boil only the water you need\n• Iron in one batch\n" +
                "• Put a boiler on a timer\n• Switch off outdoor lights you do not need, or use a motion sensor\n" +
                "• Switch things off at the wall instead of leaving them on standby\n\n" +
                "The Appliances tab shows which of your appliances cost the most.",

        "☀️ Solar basics" to
                "If you have solar, your Kenya Power meter only records what you take from the grid, so your token use is lower than your home's total use.\n\n" +
                "• Run heavy appliances (iron, washing machine, water pump) in the middle of the day when the sun is strongest\n" +
                "• Cloudy or rainy days produce less, so expect to use more from the battery or grid\n" +
                "• The Solar / Grid card on Home shows your battery level and what your panels are producing"
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_learn)
        setupBottomNav(R.id.nav_learn)

        val list = findViewById<LinearLayout>(R.id.learnList)
        for ((title, body) in topics) {
            val bodyView = TextView(this).apply {
                text = body
                textSize = 16f
                setLineSpacing(0f, 1.15f)
                setTextColor(resources.getColor(R.color.text_secondary, theme))
                setPadding(0, dp(10), 0, 0)
                visibility = View.GONE
            }
            val titleView = TextView(this).apply {
                text = title
                textSize = 17f
                setTextColor(resources.getColor(R.color.text_primary, theme))
                setTypeface(typeface, android.graphics.Typeface.BOLD)
            }
            val inner = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(18), dp(16), dp(18), dp(16))
                addView(titleView)
                addView(bodyView)
            }
            val card = MaterialCardView(this).apply {
                radius = dp(16).toFloat()
                cardElevation = 0f
                setCardBackgroundColor(resources.getColor(R.color.card_surface, theme))
                layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                    .apply { bottomMargin = dp(12) }
                isClickable = true
                setOnClickListener {
                    bodyView.visibility = if (bodyView.visibility == View.GONE) View.VISIBLE else View.GONE
                }
                addView(inner)
            }
            list.addView(card)
        }
    }
}