## Fixed
- On Linux, downloaded materials now trigger a close-write event on their final name so inotify-based sync clients such as Seafile upload them without a restart
