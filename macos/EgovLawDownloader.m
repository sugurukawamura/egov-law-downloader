#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

static NSString * const EGovBaseURL = @"https://laws.e-gov.go.jp/api/2";
static NSTimeInterval const EGovTimeout = 30.0;

static NSString *EGovSanitizeFilename(NSString *name) {
    NSCharacterSet *invalidCharacters = [NSCharacterSet characterSetWithCharactersInString:@"\\/:*?\"<>|"];
    NSArray<NSString *> *parts = [name componentsSeparatedByCharactersInSet:invalidCharacters];
    NSString *joined = [[parts componentsJoinedByString:@"_"] stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (joined.length == 0) {
        joined = @"法令";
    }
    if (joined.length > 120) {
        joined = [joined substringToIndex:120];
    }
    return joined;
}

static NSString *EGovPickDateString(NSDictionary *currentRevision, NSDictionary *lawInfo) {
    NSArray *candidates = @[
        currentRevision[@"amendment_enforcement_date"] ?: [NSNull null],
        currentRevision[@"amendment_promulgate_date"] ?: [NSNull null],
        currentRevision[@"updated"] ?: [NSNull null],
        lawInfo[@"promulgation_date"] ?: [NSNull null]
    ];

    for (id value in candidates) {
        if (![value isKindOfClass:[NSString class]]) {
            continue;
        }
        NSString *raw = (NSString *)value;
        NSArray<NSString *> *parts = [raw componentsSeparatedByString:@"-"];
        if (parts.count >= 3) {
            NSString *day = parts[2];
            if (day.length > 2) {
                day = [day substringToIndex:2];
            }
            return [NSString stringWithFormat:@"%@%@%@", parts[0], parts[1], day];
        }
    }

    NSDateFormatter *formatter = [[NSDateFormatter alloc] init];
    formatter.dateFormat = @"yyyyMMdd";
    return [formatter stringFromDate:[NSDate date]];
}

static NSString *EGovFindLawIdentifier(NSDictionary *currentRevision, NSDictionary *revisionInfo, NSDictionary *lawInfo) {
    for (NSDictionary *source in @[currentRevision, revisionInfo, lawInfo]) {
        NSString *value = source[@"law_revision_id"];
        if ([value isKindOfClass:[NSString class]] && value.length > 0) {
            return value;
        }
    }

    for (NSDictionary *source in @[lawInfo, currentRevision, revisionInfo]) {
        NSString *lawID = source[@"law_id"];
        if ([lawID isKindOfClass:[NSString class]] && lawID.length > 0) {
            return lawID;
        }

        NSString *lawNumber = source[@"law_num"];
        if ([lawNumber isKindOfClass:[NSString class]] && lawNumber.length > 0) {
            return lawNumber;
        }
    }

    return nil;
}

static NSArray<NSDictionary *> *EGovExtractLaws(id payload) {
    if ([payload isKindOfClass:[NSArray class]]) {
        return (NSArray<NSDictionary *> *)payload;
    }

    if ([payload isKindOfClass:[NSDictionary class]]) {
        NSDictionary *dictionary = (NSDictionary *)payload;
        for (NSString *key in @[@"laws", @"results", @"items", @"data"]) {
            id candidate = dictionary[key];
            if ([candidate isKindOfClass:[NSArray class]]) {
                return (NSArray<NSDictionary *> *)candidate;
            }
        }
    }

    return @[];
}

@interface EgovAppDelegate : NSObject <NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, copy) NSArray<NSDictionary *> *laws;
@property(nonatomic, copy) NSArray<NSDictionary *> *serializedLaws;
@end

@implementation EgovAppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification;

    NSRect frame = NSMakeRect(0, 0, 1180, 820);
    self.window = [[NSWindow alloc] initWithContentRect:frame
                                              styleMask:(NSWindowStyleMaskTitled |
                                                         NSWindowStyleMaskClosable |
                                                         NSWindowStyleMaskMiniaturizable |
                                                         NSWindowStyleMaskResizable)
                                                backing:NSBackingStoreBuffered
                                                  defer:NO];
    [self.window setTitle:@"e-Gov 法令ダウンローダー"];
    [self.window center];

    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    WKUserContentController *userContentController = [[WKUserContentController alloc] init];
    [userContentController addScriptMessageHandler:self name:@"egov"];
    configuration.userContentController = userContentController;

    self.webView = [[WKWebView alloc] initWithFrame:self.window.contentView.bounds configuration:configuration];
    self.webView.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    self.webView.navigationDelegate = self;
    [self.window.contentView addSubview:self.webView];

    NSURL *htmlURL = [[NSBundle mainBundle] URLForResource:@"index" withExtension:@"html"];
    NSURL *resourceURL = [htmlURL URLByDeletingLastPathComponent];
    [self.webView loadFileURL:htmlURL allowingReadAccessToURL:resourceURL];

    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    (void)sender;
    return YES;
}

- (void)userContentController:(WKUserContentController *)userContentController didReceiveScriptMessage:(WKScriptMessage *)message {
    (void)userContentController;

    if (![message.body isKindOfClass:[NSDictionary class]]) {
        return;
    }

    NSDictionary *body = (NSDictionary *)message.body;
    NSNumber *requestID = body[@"requestId"];
    NSString *action = body[@"action"];
    NSDictionary *payload = body[@"payload"];

    if (![requestID isKindOfClass:[NSNumber class]] || ![action isKindOfClass:[NSString class]]) {
        return;
    }

    if ([action isEqualToString:@"search"]) {
        [self handleSearchRequestID:requestID payload:payload];
        return;
    }

    if ([action isEqualToString:@"download"]) {
        [self handleDownloadRequestID:requestID payload:payload];
        return;
    }

    if ([action isEqualToString:@"chooseOutputDirectory"]) {
        [self handleChooseOutputDirectoryRequestID:requestID];
    }
}

- (void)handleSearchRequestID:(NSNumber *)requestID payload:(NSDictionary *)payload {
    NSString *keyword = [payload[@"keyword"] isKindOfClass:[NSString class]] ? payload[@"keyword"] : @"";
    NSNumber *limitValue = [payload[@"limit"] isKindOfClass:[NSNumber class]] ? payload[@"limit"] : @(10);

    if (keyword.length == 0) {
        [self sendError:@"法令名を入力してください。" requestID:requestID];
        return;
    }

    NSURLComponents *components = [NSURLComponents componentsWithString:[EGovBaseURL stringByAppendingString:@"/laws"]];
    components.queryItems = @[
        [NSURLQueryItem queryItemWithName:@"law_title" value:keyword],
        [NSURLQueryItem queryItemWithName:@"limit" value:[limitValue stringValue]],
        [NSURLQueryItem queryItemWithName:@"repeal_status" value:@"None"]
    ];

    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:components.URL];
    request.timeoutInterval = EGovTimeout;
    [request setValue:@"egov-law-downloader-app/1.0" forHTTPHeaderField:@"User-Agent"];

    [self performRequest:request completion:^(NSData *data, NSHTTPURLResponse *response, NSError *error) {
        if (error) {
            [self sendError:error.localizedDescription requestID:requestID];
            return;
        }

        if (response.statusCode < 200 || response.statusCode >= 300) {
            NSString *detail = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] ?: @"";
            [self sendError:[NSString stringWithFormat:@"API エラー: HTTP %ld / %@", (long)response.statusCode, detail] requestID:requestID];
            return;
        }

        NSError *jsonError = nil;
        id payloadObject = [NSJSONSerialization JSONObjectWithData:data options:0 error:&jsonError];
        if (jsonError) {
            [self sendError:@"API の返り値を読み取れませんでした。" requestID:requestID];
            return;
        }

        self.laws = EGovExtractLaws(payloadObject);
        self.serializedLaws = [self serializedLawsFromRawLaws:self.laws];
        [self sendSuccess:@{@"laws": self.serializedLaws ?: @[]} requestID:requestID];
    }];
}

- (void)handleDownloadRequestID:(NSNumber *)requestID payload:(NSDictionary *)payload {
    NSArray<NSNumber *> *indexes = [payload[@"indexes"] isKindOfClass:[NSArray class]] ? payload[@"indexes"] : @[];
    NSArray<NSString *> *fileTypes = [payload[@"fileTypes"] isKindOfClass:[NSArray class]] ? payload[@"fileTypes"] : @[];
    NSString *outputDir = [payload[@"outputDir"] isKindOfClass:[NSString class]] ? payload[@"outputDir"] : @"";
    NSString *asof = [payload[@"asof"] isKindOfClass:[NSString class]] ? payload[@"asof"] : @"";

    if (indexes.count == 0) {
        [self sendError:@"保存したい法令を 1 件以上選択してください。" requestID:requestID];
        return;
    }
    if (fileTypes.count == 0) {
        [self sendError:@"保存形式を 1 つ以上選択してください。" requestID:requestID];
        return;
    }
    if (outputDir.length == 0) {
        [self sendError:@"保存先フォルダを入力してください。" requestID:requestID];
        return;
    }

    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSMutableArray<NSString *> *logs = [NSMutableArray array];
        NSMutableArray<NSString *> *savedPaths = [NSMutableArray array];
        NSFileManager *fileManager = [NSFileManager defaultManager];
        NSError *directoryError = nil;
        [fileManager createDirectoryAtURL:[NSURL fileURLWithPath:outputDir isDirectory:YES]
              withIntermediateDirectories:YES
                               attributes:nil
                                    error:&directoryError];
        if (directoryError) {
            [self sendError:directoryError.localizedDescription requestID:requestID];
            return;
        }

        for (NSNumber *indexValue in indexes) {
            NSInteger index = indexValue.integerValue - 1;
            if (index < 0 || index >= (NSInteger)self.serializedLaws.count) {
                [self sendError:@"候補番号が範囲外です。" requestID:requestID];
                return;
            }

            NSDictionary *law = self.serializedLaws[index];
            NSString *title = law[@"title"] ?: @"名称不明";
            NSString *lawIdentifier = law[@"lawIdentifier"] ?: @"";
            NSString *dateText = law[@"date"] ?: @"";

            [logs addObject:[NSString stringWithFormat:@"対象法令: %@", title]];

            for (NSString *fileType in fileTypes) {
                [logs addObject:[NSString stringWithFormat:@"ダウンロード中: %@ (%@)", title, fileType]];
                NSError *downloadError = nil;
                NSData *data = [self downloadLawFile:lawIdentifier fileType:fileType asof:asof error:&downloadError];
                if (downloadError) {
                    [self sendError:downloadError.localizedDescription requestID:requestID];
                    return;
                }

                NSString *filename = [NSString stringWithFormat:@"%@_%@.%@", EGovSanitizeFilename(title), dateText, fileType];
                NSString *outputPath = [outputDir stringByAppendingPathComponent:filename];
                if (![data writeToFile:outputPath options:NSDataWritingAtomic error:&downloadError]) {
                    [self sendError:downloadError.localizedDescription requestID:requestID];
                    return;
                }

                [savedPaths addObject:outputPath];
                [logs addObject:[NSString stringWithFormat:@"保存完了: %@", outputPath]];
            }
        }

        [self sendSuccess:@{@"logs": logs, @"savedPaths": savedPaths} requestID:requestID];
    });
}

- (void)handleChooseOutputDirectoryRequestID:(NSNumber *)requestID {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = NO;
    panel.canChooseDirectories = YES;
    panel.allowsMultipleSelection = NO;
    panel.prompt = @"保存先を選ぶ";

    if ([panel runModal] == NSModalResponseOK && panel.URL != nil) {
        [self sendSuccess:@{@"outputDir": panel.URL.path} requestID:requestID];
    } else {
        [self sendSuccess:@{} requestID:requestID];
    }
}

- (NSArray<NSDictionary *> *)serializedLawsFromRawLaws:(NSArray<NSDictionary *> *)rawLaws {
    NSMutableArray<NSDictionary *> *items = [NSMutableArray array];
    NSInteger index = 1;

    for (NSDictionary *law in rawLaws) {
        NSDictionary *lawInfo = [law[@"law_info"] isKindOfClass:[NSDictionary class]] ? law[@"law_info"] : @{};
        NSDictionary *currentRevision = [law[@"current_revision_info"] isKindOfClass:[NSDictionary class]] ? law[@"current_revision_info"] : @{};
        NSDictionary *revisionInfo = [law[@"revision_info"] isKindOfClass:[NSDictionary class]] ? law[@"revision_info"] : @{};

        NSString *title = currentRevision[@"law_title"];
        if (![title isKindOfClass:[NSString class]] || title.length == 0) {
            title = revisionInfo[@"law_title"];
        }
        if (![title isKindOfClass:[NSString class]] || title.length == 0) {
            title = lawInfo[@"law_title"];
        }
        if (![title isKindOfClass:[NSString class]] || title.length == 0) {
            title = @"名称不明";
        }

        NSString *lawNumber = [lawInfo[@"law_num"] isKindOfClass:[NSString class]] ? lawInfo[@"law_num"] : @"";
        NSString *dateText = EGovPickDateString(currentRevision, lawInfo);
        NSString *lawIdentifier = EGovFindLawIdentifier(currentRevision, revisionInfo, lawInfo) ?: @"";

        NSMutableArray<NSString *> *parts = [NSMutableArray arrayWithObject:title];
        if (lawNumber.length > 0) {
            [parts addObject:[NSString stringWithFormat:@"法令番号: %@", lawNumber]];
        }
        [parts addObject:[NSString stringWithFormat:@"日付: %@", dateText]];

        [items addObject:@{
            @"index": @(index),
            @"title": title,
            @"lawNumber": lawNumber,
            @"date": dateText,
            @"lawIdentifier": lawIdentifier,
            @"summary": [parts componentsJoinedByString:@" | "]
        }];
        index += 1;
    }

    return items;
}

- (void)performRequest:(NSURLRequest *)request completion:(void (^)(NSData *, NSHTTPURLResponse *, NSError *))completion {
    [self performRequest:request attempt:0 completion:completion];
}

- (void)performRequest:(NSURLRequest *)request attempt:(NSInteger)attempt completion:(void (^)(NSData *, NSHTTPURLResponse *, NSError *))completion {
    NSURLSessionDataTask *task = [[NSURLSession sharedSession] dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        NSHTTPURLResponse *httpResponse = [response isKindOfClass:[NSHTTPURLResponse class]] ? (NSHTTPURLResponse *)response : nil;
        NSInteger statusCode = httpResponse.statusCode;
        BOOL shouldRetryStatus = (statusCode == 429 || statusCode == 500 || statusCode == 502 || statusCode == 503 || statusCode == 504);
        if ((error != nil || shouldRetryStatus) && attempt < 3) {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(pow(2.0, attempt) * 0.5 * NSEC_PER_SEC)), dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
                [self performRequest:request attempt:attempt + 1 completion:completion];
            });
            return;
        }
        completion(data ?: [NSData data], httpResponse, error);
    }];
    [task resume];
}

- (NSData *)downloadLawFile:(NSString *)lawIdentifier fileType:(NSString *)fileType asof:(NSString *)asof error:(NSError **)error {
    NSURLComponents *components = [NSURLComponents componentsWithString:[NSString stringWithFormat:@"%@/law_file/%@/%@", EGovBaseURL, fileType, lawIdentifier]];
    if (asof.length > 0) {
        components.queryItems = @[[NSURLQueryItem queryItemWithName:@"asof" value:asof]];
    }

    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:components.URL];
    request.timeoutInterval = EGovTimeout;
    [request setValue:@"egov-law-downloader-app/1.0" forHTTPHeaderField:@"User-Agent"];
    if ([fileType isEqualToString:@"json"]) {
        [request setValue:@"application/json" forHTTPHeaderField:@"Accept"];
    } else if ([fileType isEqualToString:@"xml"]) {
        [request setValue:@"application/xml" forHTTPHeaderField:@"Accept"];
    } else {
        [request setValue:@"*/*" forHTTPHeaderField:@"Accept"];
    }

    dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
    __block NSData *resultData = nil;
    __block NSError *resultError = nil;

    [self performRequest:request completion:^(NSData *data, NSHTTPURLResponse *response, NSError *requestError) {
        if (requestError) {
            resultError = requestError;
            dispatch_semaphore_signal(semaphore);
            return;
        }

        if (response.statusCode < 200 || response.statusCode >= 300) {
            NSString *detail = [[NSString alloc] initWithData:[data subdataWithRange:NSMakeRange(0, MIN((NSUInteger)300, data.length))] encoding:NSUTF8StringEncoding] ?: @"";
            resultError = [NSError errorWithDomain:@"EgovDownloader"
                                              code:response.statusCode
                                          userInfo:@{NSLocalizedDescriptionKey: [NSString stringWithFormat:@"取得失敗: HTTP %ld / %@", (long)response.statusCode, detail]}];
            dispatch_semaphore_signal(semaphore);
            return;
        }

        resultData = data;
        dispatch_semaphore_signal(semaphore);
    }];

    dispatch_semaphore_wait(semaphore, DISPATCH_TIME_FOREVER);
    if (error != nil) {
        *error = resultError;
    }
    return resultData;
}

- (void)sendSuccess:(NSDictionary *)payload requestID:(NSNumber *)requestID {
    [self sendToWeb:@{@"requestId": requestID, @"ok": @YES, @"payload": payload ?: @{}}];
}

- (void)sendError:(NSString *)message requestID:(NSNumber *)requestID {
    [self sendToWeb:@{@"requestId": requestID, @"ok": @NO, @"error": message ?: @"不明なエラー"}];
}

- (void)sendToWeb:(NSDictionary *)message {
    NSError *error = nil;
    NSData *jsonData = [NSJSONSerialization dataWithJSONObject:message options:0 error:&error];
    if (error) {
        return;
    }

    NSString *jsonString = [[NSString alloc] initWithData:jsonData encoding:NSUTF8StringEncoding];
    NSString *script = [NSString stringWithFormat:@"window.handleNativeResponse(%@);", jsonString];

    dispatch_async(dispatch_get_main_queue(), ^{
        [self.webView evaluateJavaScript:script completionHandler:nil];
    });
}

@end

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        NSApplication *application = [NSApplication sharedApplication];
        EgovAppDelegate *delegate = [[EgovAppDelegate alloc] init];
        application.delegate = delegate;
        return NSApplicationMain(argc, argv);
    }
}
