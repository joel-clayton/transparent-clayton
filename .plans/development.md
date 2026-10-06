This document will outline the development plans for this project.

# Current status
The current application (as of git hash adc764d8ca611b8cfb63ce464ece98fe2b34b576) is capable of automatically processing:
- City Council meetings
- fully locally, relying on local storage of downloaded and generated assets
- specific to Clayton, CA

# Phased Development Plans
## Phase 2
This application should be fully automated to handle any event/meeting posted on the city website, whether it has assets like documents or video or not. All events should be dealt with appropriately, whether using the full ingest pipeline or just parts of it or ignoring the event completely.

Things to be considered carefully:
- which combination of assets warrant which type of behavior
- mapping differing event types to a consistent wiki representation
- Documents from meetings should also be archived on disk, instead of simply linked in the wiki to the city website

#### Success Measure
1. This pipeline should run smoothly and automatically, generating assets when and where expected, without intervention. First production test of this is September 22, 2026.
2. Backfill of an individual asset type should be possible over any length of history, e.g. downloading to disk all meeting documents for meetings that have otherwise been processed already

## Phase 3
With all the component parts in place, the application should be become agnostic to how the video and transcript data are consumed and offering an alternative to the combination of Google Docs, YouTube, and a MediaWiki.

Generated video and text assets along with documents from meetings themselves should live on disk. That is, all artifacts that are posted to the wiki should also live on disk, organized appropriately.

This means:
- making the WikiUpdater class and its place in the workflow optional, and
- making the Google Docs and YouTube uploads optional, and
- offering a locally deployed, multi-tenant server option for interacting with the data as an alternative, or no publishing option at all
- organizing meetings transcripts in the correct buckets in Google Docs
- organizing meeting video backups in the correct playlists in YouTube
- organizing meeting assets on disk in categories that match their handling elsewhere

There are multiple open source options for a local server with LLM-enhanced chat and organization options (e.g. LibreChat, OpenWebUI), and one should be chosen as the preferred solution to ship with.

#### Success Measure(s)
1. Reconciliation script should recognize assets on disk and in their publishing locations without any gaps
2. Usage of particular publishing destinations should be configurable
3. Reconciliation script should recognize the local CMS's vector DB as a publishing location, when configured
4. Pointing the application at a fresh install of the CMS should populate the appropriate data in the CMS and allow for LLM-enhanced RAG operations

## Phase 4
This application should be generalized to work for any city in California.

Basic requirement:
- Municipality has a website where details are posted, whether via a CMS or plain HTML

Different counties/cities have different laws or practices (or a lack thereof) around transparency of local government.

#### Success Measure
Installing the application in a separate directory and pointing it at a different city should result in a successful dry run.

## Phase 5
This application should become a single, installable package. Its core functionality will include:
- the classes needed to consume and organize public meeting data
- the option to upload assets to Google Docs, YouTube, MediaWiki, and/or a local server-based content management system (installed separately).

Configuration requirements will include:
- Google Apps credentials (for managing authentication tokens)
- Google Docs credentials
- YouTube credentials
- MediaWiki credentials
- Local CMS server connection credentials

### Success Measure
A technical user in a different California city with no prior knowledge of the project should be able to spin it up, provide necessary details and credentials, and have a working automated pipeline.

## Phase 6
The application's infrastructure should be generalized to allow for local storage OR cloud storage. Any other considerations for development flexibility, happiness, and economy should be prioritized.

### Success Measure
Project owner should be able to spin up a second instance of the project on a different computer with the necessary credentials and have a working automated pipeline.

## Phase 7
This generalized, automated application should be open sourced under project owner's personal account. The current instance for Clayton, CA should shift to being an installation of that open sourced project.

## Phase 8
Consider the feasibility of running an application instance on an internet connected Raspberry Pi (or another affordable pocket computer) with an SSD thumb drive and an internet connection.

## Phase 9
Consider the costs and complexity of running a cloud instance that spans many California cities.
