# Training a custom wake word

Abogi's tray listens for a wake word on this computer (openWakeWord) before
anything is streamed. The built-in model is "Hey Jarvis". Any other phrase,
such as "Hey Abboggy" (said like the Korean 아버지, "father"), needs its own
small model.

## 1. Train the model (about an hour, in the browser)

1. Open [openWakeWord's training notebook](https://colab.research.google.com/drive/1q1oe2zOyZp7UsB3jJiQ1IFn8z5YfjwEb?usp=sharing)
   in Google Colab and choose a GPU runtime (Runtime > Change runtime type > T4 GPU).
2. Set the target phrase. The training clips come from an English text-to-speech
   voice, so spell the phrase the way an English speaker would read it aloud.
   Where the notebook takes a list of phrases, give a few spellings, since a
   model fires on any of them:
   - `hey abboggy` (first: the model file is named after it)
   - `hey ah bo gee`
   - `hey abogi`
   - `hey ah buh jee` (closer to 아버지)
3. Run all cells. When training finishes, download `hey_abboggy.onnx`.

openWakeWord's notes say a model trained in the quick notebook may be less
accurate than one trained with more clips (its full pipeline,
`notebooks/automatic_model_training.ipynb`, suggests 20,000+ examples). If it
misses you or fires on its own, retrain with more examples or steps.

## 2. Point Abogi at it

Save the file outside this repository (for example in your user folder), then
in `.env`:

```
ABOGI_VOICE_MODE=wake-word
ABOGI_WAKE_WORD=C:/Users/<you>/abogi/hey_abboggy.onnx
```

(Forward slashes avoid any escaping in `.env`; Windows accepts them.)

Restart the tray. The menu shows "Wake word ("Hey Abboggy")", and the status
reads `Ready — say "Hey Abboggy"`. A missing file is logged as
"No wake word model at ..." and the tray stays usable with push-to-talk.

The approval phrase is unchanged: say "Abogi, approve" (only "approve" at the
end of a short phrase counts, so the name may be heard any way).
