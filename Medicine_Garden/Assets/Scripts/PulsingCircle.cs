using UnityEngine;
using System;

public class PulsingCircle : MonoBehaviour
{
    public float pulseSpeed = 2f;         // Speed of the pulse
    public float pulseAmount = 0.05f;      // Size variation
    public float baseScale = 1f;          // Original size

    private Vector3 originalScale;

    void Start()
    {
        originalScale = Vector3.one * baseScale;
        transform.localScale = originalScale;
    }

    void Update()
    {
        float scaleOffset = Mathf.Sin(Time.time * pulseSpeed) * pulseAmount;
        transform.localScale = originalScale + Vector3.one * scaleOffset;
    }
}
